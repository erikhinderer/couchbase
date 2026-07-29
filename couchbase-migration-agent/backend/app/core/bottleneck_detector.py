"""
Best-effort, read-only detection of common cbbackupmgr backup/restore bottlenecks
while a migration is actively running, based on Couchbase's own published guidance:

  - "Troubleshooting Slow Couchbase Backup and Restore Processes"
    https://support.couchbase.com/hc/en-us/articles/24941535204763
    -- names CPU/memory pressure on Data Service nodes, thread count vs. available
    CPUs, and network/NFS instability as the primary causes of slow backup/restore,
    and gives --threads / --data-rate-limit as the two levers to adjust.

  - "Manage Backup Service Threads" (thread-vs-CPU sizing formula)
    https://docs.couchbase.com/server/current/rest-api/backup-node-threads.html
    -- the Backup Service's own default thread count is max(1, cpu_cores * 0.75);
    this module reuses that formula as a concrete, non-judgment-call threshold for
    flagging an oversubscribed --threads setting.

SCOPE NOTE: this module only ever DETECTS. It never touches a running process's
flags directly -- the nodesThreadsMap REST API in the second article above belongs
to Couchbase's persistent Backup Service, not the standalone cbbackupmgr CLI this
app drives, and a CLI process's --threads/--data-rate-limit are fixed at launch and
can't be retuned live. What CAN be automated is the same thing a human would do
about a thread-actionable finding: stop the process and relaunch it with a lower
--threads value. For the BACKUP phase (the source cluster, whose backup subprocess
this app itself launched and fully controls), MigrationEngine.backup_source() does
exactly that automatically for CPU_SATURATED and MEMORY_PRESSURE findings, using
recommended_threads (below) as the new value, bounded by MIN_AUTO_THROTTLE_THREADS
and MAX_AUTO_THROTTLE_ATTEMPTS -- both are real, currently-observed pressure on the
source. THREAD_OVERSUBSCRIBED deliberately never sets recommended_threads and is
never auto-acted on, even during backup: it fires purely from the static
cpu_cores*0.75 formula being exceeded, which can (and in testing did) trigger at
CPU utilization as low as 11% -- auto-restarting a backup on that alone would be
acting without the source actually being under load, which defeats the point of
auto-throttling in the first place. Everything else -- THREAD_OVERSUBSCRIBED (as
just covered), THROUGHPUT_STALLED/THROUGHPUT_DEGRADED (not a threads problem; see
their suggestion text) on either phase, and any finding during RESTORE (the
destination side isn't a process this app launched and can safely kill/relaunch
mid-flight the way it can its own backup subprocess -- restoring already has its
own map-data retry loop with different failure semantics) -- stays a suggestion in
the Ask The Agent panel for a person to act on.

Findings are appended to MigrationRecord.bottleneck_findings by the caller
(MigrationEngine) and broadcast over the existing per-migration websocket, where the
frontend's Ask The Agent panel picks up new ones and surfaces them proactively --
auto-remediated ones as a "here's what I just did" notice, everything else as a
suggestion.
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field

from app.models.enums import BottleneckKind

# A fresh BottleneckMonitor is created per cbbackupmgr invocation this engine drives
# (one per backup, one per restore) -- there's no need for its rolling state to
# outlive that single run.

# Node stats are a REST round-trip against the cluster's own management API; polling
# on every progress tick (which can arrive multiple times a second) would be wasteful
# and could itself add load to a cluster that's already under pressure. Throughput-
# trend checks are pure local arithmetic and can run every tick instead.
STATS_POLL_INTERVAL_S = 20.0

# "Essentially zero" throughput, sustained, is a stronger signal than "slow" -- it
# usually means a stuck connection rather than a merely under-resourced transfer.
STALL_THRESHOLD_MBPS = 0.05
STALL_WINDOW_S = 45.0

# A run that's dropped to well below its own peak (not "slow relative to some fixed
# number", which varies wildly by dataset/hardware) is what the support article's
# CPU/memory/network causes actually look like in the numbers this app has access to.
DEGRADED_RATIO = 0.4
DEGRADED_MIN_PEAK_MBPS = 0.5  # don't judge "degraded" against a peak that was never meaningful
DEGRADED_WINDOW_S = 60.0

# How long a given finding kind stays suppressed after firing, so a bottleneck that
# persists for minutes doesn't repost itself (and re-open the agent panel) every tick.
FINDING_COOLDOWN_S = 120.0

HIGH_CPU_PCT = 88.0
LOW_MEM_FREE_RATIO = 0.08  # Couchbase Server REST reports mem_free/mem_total in bytes

# Auto-throttle safety rails for the BACKUP phase (see module docstring). Never go
# below one thread, and cap how many times a single backup run will stop-and-
# relaunch itself -- if the cluster is still saturated after MAX_AUTO_THROTTLE_ATTEMPTS
# reductions, that's no longer a thread-count problem this app can fix by itself, and
# MigrationEngine leaves the last detection finding as a plain suggestion instead of
# requesting another restart.
MIN_AUTO_THROTTLE_THREADS = 1
MAX_AUTO_THROTTLE_ATTEMPTS = 3


@dataclass
class NodeResourceStats:
    hostname: str
    cpu_utilization_pct: float
    cpu_cores: int
    mem_total_bytes: int
    mem_free_bytes: int


@dataclass
class _RawFinding:
    kind: BottleneckKind
    message: str
    suggestion: str
    # Set only for CPU_SATURATED/MEMORY_PRESSURE -- real, observed pressure on the
    # source -- so the caller can auto-throttle a backup rather than just posting a
    # suggestion. Deliberately left None for THREAD_OVERSUBSCRIBED (fires from a
    # static formula, not observed load; see stats_findings()) and for anything a
    # threads change can't fix (THROUGHPUT_STALLED/THROUGHPUT_DEGRADED).
    recommended_threads: int | None = None


@dataclass
class BottleneckMonitor:
    """Per-run rolling state. Feed it throughput samples via observe(); call
    local_findings() every tick (cheap) and, gated by should_poll_stats(), fetch node
    stats and pass them to stats_findings() (a REST round-trip, done by the caller so
    this class stays free of any I/O/client dependency)."""

    _samples: deque[tuple[float, float]] = field(default_factory=lambda: deque(maxlen=120))
    _peak_mbps: float = 0.0
    _stall_started_at: float | None = None
    _degraded_started_at: float | None = None
    _last_stats_poll: float = 0.0
    _last_finding_at: dict[BottleneckKind, float] = field(default_factory=dict)

    def observe(self, mbps: float, elapsed_s: float) -> None:
        now = time.monotonic()
        self._samples.append((now, mbps))
        if mbps > self._peak_mbps:
            self._peak_mbps = mbps

        if mbps < STALL_THRESHOLD_MBPS and elapsed_s > 5:
            if self._stall_started_at is None:
                self._stall_started_at = now
        else:
            self._stall_started_at = None

        if self._peak_mbps >= DEGRADED_MIN_PEAK_MBPS and mbps < self._peak_mbps * DEGRADED_RATIO:
            if self._degraded_started_at is None:
                self._degraded_started_at = now
        else:
            self._degraded_started_at = None

    def _eligible(self, kind: BottleneckKind) -> bool:
        last = self._last_finding_at.get(kind)
        return last is None or (time.monotonic() - last) >= FINDING_COOLDOWN_S

    def _mark(self, kind: BottleneckKind) -> None:
        self._last_finding_at[kind] = time.monotonic()

    def local_findings(self) -> list[_RawFinding]:
        """Throughput-trend findings. No I/O -- safe to call on every progress tick."""
        out: list[_RawFinding] = []
        now = time.monotonic()

        if (
            self._stall_started_at is not None
            and (now - self._stall_started_at) >= STALL_WINDOW_S
            and self._eligible(BottleneckKind.THROUGHPUT_STALLED)
        ):
            stalled_for = int(now - self._stall_started_at)
            out.append(_RawFinding(
                kind=BottleneckKind.THROUGHPUT_STALLED,
                message=(
                    f"Transfer throughput has been essentially flat (under "
                    f"{STALL_THRESHOLD_MBPS:.2f} MB/s) for over {stalled_for}s."
                ),
                suggestion=(
                    "This usually isn't a thread/rate setting -- check that the connection to "
                    "the cluster is still healthy (network blip, VPN drop, or the cluster itself "
                    "stalling) before changing any backup/restore flags. If connectivity comes "
                    "back, cbbackupmgr's --resume flag can pick a restore back up from where it "
                    "left off rather than starting over."
                ),
            ))
            self._mark(BottleneckKind.THROUGHPUT_STALLED)

        if (
            self._degraded_started_at is not None
            and (now - self._degraded_started_at) >= DEGRADED_WINDOW_S
            and self._eligible(BottleneckKind.THROUGHPUT_DEGRADED)
        ):
            degraded_for = int(now - self._degraded_started_at)
            out.append(_RawFinding(
                kind=BottleneckKind.THROUGHPUT_DEGRADED,
                message=(
                    f"Throughput has dropped well below this run's own peak (currently under "
                    f"{self._peak_mbps * DEGRADED_RATIO:.1f} MB/s against a peak of "
                    f"{self._peak_mbps:.1f} MB/s) for over {degraded_for}s."
                ),
                suggestion=(
                    "Per Couchbase's backup/restore troubleshooting guidance, a sustained drop "
                    "like this usually points to CPU or memory contention on the cluster, or an "
                    "under-provisioned network path -- not something adding threads would fix. "
                    "Check node CPU/memory below (if available) before changing --threads; if "
                    "resources look fine, this is worth treating as a network issue rather than "
                    "a backup/restore configuration one."
                ),
            ))
            self._mark(BottleneckKind.THROUGHPUT_DEGRADED)

        return out

    def should_poll_stats(self) -> bool:
        now = time.monotonic()
        if now - self._last_stats_poll >= STATS_POLL_INTERVAL_S:
            self._last_stats_poll = now
            return True
        return False

    def stats_findings(
        self, nodes: list[NodeResourceStats], configured_threads: int, cluster_label: str,
    ) -> list[_RawFinding]:
        """CPU/memory/thread-sizing findings from a fresh node-stats poll. Judges the
        busiest relevant node only -- a single hot node is enough to bottleneck the
        whole transfer even if others are idle."""
        if not nodes:
            return []
        out: list[_RawFinding] = []
        busiest = max(nodes, key=lambda n: n.cpu_utilization_pct)
        recommended_threads = max(1, round(busiest.cpu_cores * 0.75)) if busiest.cpu_cores else None

        if busiest.cpu_utilization_pct >= HIGH_CPU_PCT and self._eligible(BottleneckKind.CPU_SATURATED):
            sizing_note = (
                f"Couchbase's own Backup Service default sizing is max(1, cpu_cores × 0.75) = "
                f"{recommended_threads} thread(s) for this node's {busiest.cpu_cores} core(s); "
                f"this run is configured for {configured_threads}."
                if recommended_threads is not None
                else f"this run is configured for {configured_threads} thread(s)."
            )
            out.append(_RawFinding(
                kind=BottleneckKind.CPU_SATURATED,
                message=(
                    f"Node {busiest.hostname} on {cluster_label} is at "
                    f"{busiest.cpu_utilization_pct:.0f}% CPU while this transfer is running."
                ),
                suggestion=(
                    f"{sizing_note} If the configured thread count is already at or above the "
                    "recommended value, lowering --threads (or capping --data-rate-limit) for "
                    "the next attempt is more likely to help than hurt -- more threads than the "
                    "cluster's CPU can support usually adds contention, not throughput."
                ),
                recommended_threads=recommended_threads,
            ))
            self._mark(BottleneckKind.CPU_SATURATED)
        elif (
            recommended_threads is not None
            and configured_threads > recommended_threads
            and self._eligible(BottleneckKind.THREAD_OVERSUBSCRIBED)
        ):
            out.append(_RawFinding(
                kind=BottleneckKind.THREAD_OVERSUBSCRIBED,
                message=(
                    f"This transfer is configured for {configured_threads} thread(s), above "
                    f"Couchbase's own default sizing of max(1, cpu_cores × 0.75) = "
                    f"{recommended_threads} for {busiest.hostname}'s {busiest.cpu_cores} core(s) "
                    f"on {cluster_label}."
                ),
                suggestion=(
                    f"CPU isn't saturated yet ({busiest.cpu_utilization_pct:.0f}%), but more "
                    "threads than this typically doesn't add throughput and can add contention "
                    f"as the run progresses -- consider {recommended_threads} thread(s) for "
                    "future runs against this cluster."
                ),
                # Deliberately NOT set: unlike CPU_SATURATED/MEMORY_PRESSURE (both real,
                # observed pressure on the source), this fires purely off the static
                # cpu_cores*0.75 formula being exceeded -- it can and does fire at low
                # CPU (seen at 11% in testing). Auto-restarting a backup on that alone
                # would mean action without the source actually being under load, which
                # defeats the "protect the source's real performance" point of
                # auto-throttling -- see the module docstring's SCOPE NOTE. Stays a
                # suggestion the user can act on for future runs.
                recommended_threads=None,
            ))
            self._mark(BottleneckKind.THREAD_OVERSUBSCRIBED)

        if busiest.mem_total_bytes:
            free_ratio = busiest.mem_free_bytes / busiest.mem_total_bytes
            if free_ratio < LOW_MEM_FREE_RATIO and self._eligible(BottleneckKind.MEMORY_PRESSURE):
                out.append(_RawFinding(
                    kind=BottleneckKind.MEMORY_PRESSURE,
                    message=(
                        f"Node {busiest.hostname} on {cluster_label} has only "
                        f"{busiest.mem_free_bytes / (1024 ** 3):.1f} GiB free "
                        f"({free_ratio:.0%} of total) while this transfer is running."
                    ),
                    suggestion=(
                        "Per Couchbase's troubleshooting guidance, memory pressure during "
                        "backup/restore usually tracks thread count and overall cluster load -- "
                        "lowering --threads is the one lever available here; if pressure persists "
                        "at low thread counts too, that's worth a memory profile and a Couchbase "
                        "Support case rather than a flag change."
                    ),
                    recommended_threads=recommended_threads,
                ))
                self._mark(BottleneckKind.MEMORY_PRESSURE)

        return out
