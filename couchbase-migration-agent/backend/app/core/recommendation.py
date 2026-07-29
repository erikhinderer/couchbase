"""
Replication-mode recommendation for the "Destination & Mode" wizard step.

The wizard asks the user one question -- do you plan to cut every application
over to the destination at once, or migrate them gradually (a phased cutover)?
-- and combines the answer with the already-introspected source topology
(XDCR usage, bucket count/sizes, total data size) to recommend one of the
three MigrationStrategy options, with a plain-language rationale and a rough
transfer-duration estimate.

DESIGN NOTE: this is deliberately a deterministic, rule-based recommender, not
a live call to the local Qwen LLM. The rest of this app already leans toward
fast, explainable, non-LLM logic for anything that gates a real
infrastructure decision (see bottleneck_detector.py's auto-throttle, which is
also pure rules) -- a wizard step is on the critical path of setting up a
migration, so it shouldn't be exposed to LLM latency or the (small but real)
risk of a hallucinated recommendation. It's still framed in the app's "agent"
voice for consistency with the rest of the UI.

The duration estimate is explicitly a rough planning figure, not a
measurement -- actual cbbackupmgr throughput depends heavily on network,
disk, and cluster load that can't be known ahead of time. It exists to give
the "cutover all at once" question a concrete number to reason about (is a
single maintenance window realistic?), not to be quoted as a guarantee.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from app.models.enums import MigrationStrategy
from app.models.schemas import ClusterTopologySnapshot

CutoverPlan = Literal["cutover", "phased"]

# Rough, deliberately conservative combined backup+restore throughput assumption
# per cbbackupmgr thread, used only to turn a data size into a ballpark duration
# for planning purposes. Real throughput varies enormously with network path,
# disk speed, and cluster load -- see the module docstring.
ASSUMED_MB_PER_SEC_PER_THREAD = 8.0
# --threads scales throughput sub-linearly in practice (contention, per-node
# connection limits) -- cap how many "effective" threads count toward the
# estimate rather than assuming a naive linear speedup.
MAX_EFFECTIVE_THREADS_FOR_ESTIMATE = 8

# Below this, a single one-time backup/restore comfortably fits in one
# maintenance window; at or above it, staging most of the data ahead of time
# (HYBRID) shrinks the actual cutover window even when every application is
# still switching over at the same moment.
SHORT_WINDOW_SECONDS = 2 * 3600  # 2 hours

# A single bucket this dominant in total size is worth calling out explicitly
# (e.g. worth extra attention to that bucket's own transfer time/threading).
DOMINANT_BUCKET_SIZE_RATIO = 0.6


@dataclass
class ReplicationModeRecommendation:
    recommended_strategy: MigrationStrategy
    headline: str
    rationale: str
    considerations: list[str] = field(default_factory=list)
    estimated_duration_seconds: float | None = None


def _effective_mbps(parallelism: int) -> float:
    threads = max(1, min(parallelism, MAX_EFFECTIVE_THREADS_FOR_ESTIMATE))
    return ASSUMED_MB_PER_SEC_PER_THREAD * threads


def _estimate_one_time_duration_seconds(total_bytes: int, parallelism: int) -> float:
    """Backup + restore modeled as two sequential phases of similar throughput --
    a one-time migration has to fully back up the source, then fully restore
    into the destination, before it's done."""
    mb = max(total_bytes, 0) / (1024 * 1024)
    mbps = _effective_mbps(parallelism)
    if mbps <= 0:
        return 0.0
    return (mb / mbps) * 2


def _format_hours(seconds: float) -> str:
    hours = seconds / 3600
    if hours < 1:
        return f"~{max(1, round(seconds / 60))} minutes"
    if hours < 48:
        return f"~{hours:.1f} hours"
    return f"~{hours / 24:.1f} days"


def recommend_replication_mode(
    cutover_plan: CutoverPlan,
    topology: ClusterTopologySnapshot,
    parallelism: int,
) -> ReplicationModeRecommendation:
    total_bytes = topology.total_data_size_bytes or 0
    bucket_count = len(topology.buckets)
    xdcr_in_use = bool(topology.xdcr_remotes)
    estimated_seconds = _estimate_one_time_duration_seconds(total_bytes, parallelism)

    considerations: list[str] = []
    if xdcr_in_use:
        remote_names = ", ".join(r.name for r in topology.xdcr_remotes)
        considerations.append(
            f"The source already has active XDCR replication to {len(topology.xdcr_remotes)} "
            f"remote cluster(s) ({remote_names}). Plan to reconfigure or decommission those "
            "once the source is no longer the system of record."
        )
    if bucket_count > 1 and total_bytes > 0 and topology.per_bucket_stats:
        largest = max(topology.per_bucket_stats.items(), key=lambda kv: kv[1].get("data_size_bytes", 0))
        largest_name, largest_stats = largest
        largest_bytes = largest_stats.get("data_size_bytes", 0)
        if largest_bytes / total_bytes >= DOMINANT_BUCKET_SIZE_RATIO:
            considerations.append(
                f"Bucket \"{largest_name}\" accounts for {largest_bytes / total_bytes:.0%} of the "
                "source's total data size -- it will dominate the transfer time; consider whether "
                "it alone might benefit from a dedicated migration window."
            )
    if bucket_count > 10:
        considerations.append(
            f"{bucket_count} buckets detected. --threads is shared across all of them "
            "concurrently, so a larger thread count matters more here than for a single-bucket "
            "migration."
        )

    if cutover_plan == "phased":
        strategy = MigrationStrategy.HYBRID
        headline = "Bulk copy + continuous sync (phased cutover)"
        rationale = (
            "You're planning to move applications over gradually rather than all at once, which "
            "means source and destination both need to stay in sync for a while -- a one-time "
            "snapshot can't do that; the moment the first application starts writing to the "
            "destination, a plain backup/restore would silently start missing new source writes. "
            "Bulk copy + continuous sync moves the existing data over quickly with an initial "
            "cbbackupmgr restore, then XDCR keeps both clusters converged so each application can "
            "cut over on its own schedule, in any order."
        )
        if total_bytes < 50 * 1024 * 1024:  # under ~50 MiB, bulk copy saves little
            considerations.append(
                "The source currently has very little data, so the bulk-copy step will finish "
                "almost immediately -- continuous replication (XDCR_LIVE) reaches the same "
                "end state with one fewer moving part if you'd rather skip it."
            )
    else:  # "cutover"
        if estimated_seconds < SHORT_WINDOW_SECONDS:
            strategy = MigrationStrategy.BACKUP_RESTORE
            headline = "One-time migration (single maintenance window)"
            rationale = (
                "You're planning to cut every application over at the same time, and the "
                f"estimated transfer time ({_format_hours(estimated_seconds)}) comfortably fits "
                "a single maintenance window. A one-time backup/restore is the simplest option "
                "here -- there's no ongoing replication to configure, monitor, or tear down "
                "afterward."
            )
        else:
            strategy = MigrationStrategy.HYBRID
            headline = "Bulk copy + continuous sync (shrink the cutover window)"
            rationale = (
                "You're planning to cut every application over at the same time, but the "
                f"estimated one-time transfer ({_format_hours(estimated_seconds)}) would mean a "
                "long outage window if done as a single backup/restore. Bulk copy + continuous "
                "sync moves the bulk of the data ahead of time via an initial restore, then XDCR "
                "keeps the destination current in the background -- the actual cutover, when you "
                "stop XDCR and flip applications over, only has to cover whatever changed since "
                "the bulk copy finished, which is typically a much shorter window."
            )
            considerations.append(
                "This still results in every application cutting over at the same moment -- "
                "only the data transfer is staged, not the application switchover itself."
            )

    return ReplicationModeRecommendation(
        recommended_strategy=strategy,
        headline=headline,
        rationale=rationale,
        considerations=considerations,
        estimated_duration_seconds=estimated_seconds if estimated_seconds > 0 else None,
    )
