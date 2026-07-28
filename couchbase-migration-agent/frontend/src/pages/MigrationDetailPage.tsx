import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import {
  getMigration,
  approveMigration,
  startMigration,
  rollbackMigration,
  stopReplication,
} from "@/api/client";
import { useMigrationSocket } from "@/hooks/useMigrationSocket";
import ClusterTopologyDiagram from "@/components/topology/ClusterTopologyDiagram";
import StatCard from "@/components/dashboard/StatCard";
import ThroughputChart from "@/components/dashboard/ThroughputChart";
import ValidationResults from "@/components/validation/ValidationResults";
import { RotateCcw, PlayCircle, ShieldCheck, GitBranch, Square } from "lucide-react";

const PHASE_BADGE: Record<string, string> = {
  complete: "cb-badge-success",
  migrating: "cb-badge-progress",
  replicating: "cb-badge-progress",
  backup_in_progress: "cb-badge-progress",
  validating: "cb-badge-progress",
  awaiting_approval: "cb-badge-warning",
  validation_failed: "cb-badge-error",
  backup_failed: "cb-badge-error",
  failed: "cb-badge-error",
  rolled_back: "cb-badge-warning",
  stopped: "cb-badge-warning",
};

export default function MigrationDetailPage() {
  const { id } = useParams<{ id: string }>();
  const [record, setRecord] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const { data: live } = useMigrationSocket(id || "*");

  useEffect(() => {
    if (id) getMigration(id).then(setRecord).catch(() => {});
  }, [id]);

  useEffect(() => {
    if (live && (live as any).migration_id === id) setRecord(live);
  }, [live, id]);

  if (!record) return <div style={{ padding: 32, color: "var(--text-muted)" }}>Loading…</div>;

  const { plan, phase, stats, validation_report, backup_record, log_tail } = record;
  const isContinuousStrategy = plan.strategy === "xdcr_live" || plan.strategy === "hybrid";
  const isReplicating = phase === "replicating";
  const canApprove = phase === "awaiting_approval";
  const canStart = phase === "approved";
  const canRollback = backup_record?.status === "complete" && !["rolling_back", "rolled_back"].includes(phase);

  async function act(fn: () => Promise<void>) {
    setBusy(true);
    try {
      await fn();
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={{ padding: 32 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 20 }}>
        <div>
          <h1 style={{ fontSize: 20, marginBottom: 4 }}>{plan.name}</h1>
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <span className={`cb-badge ${PHASE_BADGE[phase] || "cb-badge-info"}`}>{phase}</span>
            {isContinuousStrategy && (
              <span className="cb-badge cb-badge-info" style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
                <GitBranch size={11} /> continuous replication
              </span>
            )}
          </div>
        </div>
        <div style={{ display: "flex", gap: 10 }}>
          {canApprove && (
            <button
              className="cb-btn cb-btn-primary"
              disabled={busy}
              onClick={() => act(async () => setRecord(await approveMigration(id!, "erikhinderer@gmail.com")))}
            >
              <ShieldCheck size={14} /> Approve
            </button>
          )}
          {canStart && (
            <button className="cb-btn cb-btn-primary" disabled={busy} onClick={() => act(async () => setRecord(await startMigration(id!)))}>
              <PlayCircle size={14} /> Start {isContinuousStrategy ? "replication" : "migration"}
            </button>
          )}
          {isReplicating && (
            <>
              <button
                className="cb-btn cb-btn-primary"
                disabled={busy}
                onClick={() => act(async () => setRecord(await stopReplication(id!, true)))}
                title="Stop replication and mark the destination as authoritative"
              >
                <ShieldCheck size={14} /> Cutover & complete
              </button>
              <button
                className="cb-btn"
                disabled={busy}
                onClick={() => act(async () => setRecord(await stopReplication(id!, false)))}
                title="Stop replication without cutover; source remains authoritative"
              >
                <Square size={14} /> Stop replication
              </button>
            </>
          )}
          {canRollback && (
            <button
              className="cb-btn cb-btn-danger"
              disabled={busy}
              onClick={() => act(async () => { await rollbackMigration(id!, "user_requested"); setRecord(await getMigration(id!)); })}
            >
              <RotateCcw size={14} /> Rollback source
            </button>
          )}
        </div>
      </div>

      <div style={{ marginBottom: 20 }}>
        <ClusterTopologyDiagram
          source={validation_report?.source_topology && {
            label: plan.source.label,
            version: validation_report.source_topology.cluster_version,
            nodes: validation_report.source_topology.nodes,
            buckets: validation_report.source_topology.buckets,
            // Only surface XDCR remotes on the diagram when this migration is actually
            // using a continuous (XDCR-based) strategy. The source cluster may have
            // other, unrelated XDCR replications already configured (e.g. left over
            // from a previous migration attempt, or pre-existing on real infra) --
            // showing that satellite node for a plain one-time backup/restore
            // migration was misleading, implying this migration involved XDCR when it
            // doesn't.
            xdcrRemotes: isContinuousStrategy ? validation_report.source_topology.xdcr_remotes : undefined,
          }}
          destination={validation_report?.dest_topology && {
            label: plan.destination.label,
            isCapella: plan.destination.is_capella,
            nodes: validation_report.dest_topology.nodes,
            buckets: validation_report.dest_topology.buckets,
          }}
          phase={phase}
          throughputMbPerSec={stats?.throughput_mb_per_sec}
        />
      </div>

      {isReplicating ? (
        <div style={{ display: "flex", gap: 14, marginBottom: 20, flexWrap: "wrap" }}>
          <StatCard label="Mutations replicated" value={stats.mutations_replicated.toLocaleString()} accent="var(--cb-teal)" />
          <StatCard label="Mutations / sec" value={stats.mutations_per_sec.toFixed(1)} />
          <StatCard label="Changes left" value={(stats.changes_left ?? 0).toLocaleString()} accent={stats.changes_left ? "var(--status-warning)" : "var(--status-success)"} />
          <StatCard
            label="Est. time to catch up"
            value={stats.replication_lag_seconds ? formatDuration(stats.replication_lag_seconds) : "caught up"}
          />
          <StatCard label="Running for" value={formatDuration(stats.elapsed_seconds)} />
          <StatCard label="Replication" value={stats.replication_active ? "Active" : "Idle"} accent={stats.replication_active ? "var(--status-success)" : "var(--status-warning)"} />
        </div>
      ) : (
        <div style={{ display: "flex", gap: 14, marginBottom: 20, flexWrap: "wrap" }}>
          <StatCard label="Docs migrated" value={`${stats.docs_migrated.toLocaleString()} / ${stats.docs_total.toLocaleString()}`} />
          <StatCard label="Throughput" value={`${stats.throughput_mb_per_sec.toFixed(1)} MB/s`} accent="var(--cb-teal)" />
          <StatCard label="Docs / sec" value={stats.throughput_docs_per_sec.toFixed(0)} />
          <StatCard label="Error rate" value={`${stats.error_rate_pct.toFixed(2)}%`} accent={stats.error_rate_pct > 0 ? "var(--status-error)" : undefined} />
          <StatCard label="Elapsed" value={formatDuration(stats.elapsed_seconds)} />
          <StatCard label="ETA" value={stats.eta_seconds ? formatDuration(stats.eta_seconds) : "—"} />
        </div>
      )}

      <div style={{ marginBottom: 20 }}>
        <ThroughputChart
          mbps={isReplicating ? stats.mutations_per_sec : stats.throughput_mb_per_sec}
          docsPerSec={stats.throughput_docs_per_sec}
          label={isReplicating ? "REPLICATION RATE (mutations/sec)" : "THROUGHPUT (MB/s)"}
        />
      </div>

      {validation_report?.checks?.length > 0 && (
        <div style={{ marginBottom: 20 }}>
          <h3 style={{ fontSize: 14, marginBottom: 10 }}>Validation</h3>
          <ValidationResults checks={validation_report.checks} />
        </div>
      )}

      <div className="cb-card" style={{ padding: 14 }}>
        <h3 style={{ fontSize: 13, marginBottom: 8, color: "var(--text-secondary)" }}>Log</h3>
        <div style={{ fontFamily: "var(--font-mono)", fontSize: 11, maxHeight: 220, overflow: "auto", color: "var(--text-muted)" }} className="cb-scrollbar">
          {(log_tail || []).map((l: string, i: number) => <div key={i}>{l}</div>)}
        </div>
      </div>
    </div>
  );
}

function formatDuration(s: number): string {
  if (!s) return "0s";
  const m = Math.floor(s / 60);
  const sec = Math.round(s % 60);
  const h = Math.floor(m / 60);
  if (h > 0) return `${h}h ${m % 60}m`;
  return m > 0 ? `${m}m ${sec}s` : `${sec}s`;
}
