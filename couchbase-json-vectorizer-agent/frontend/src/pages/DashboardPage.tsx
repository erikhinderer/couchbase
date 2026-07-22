import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { getDashboardStats, stopJob } from "@/api/client";
import { useVectorizerSocket } from "@/hooks/useVectorizerSocket";
import StatCard from "@/components/dashboard/StatCard";
import VectorizationRateChart from "@/components/dashboard/VectorizationRateChart";

const PHASE_BADGE: Record<string, string> = {
  ready: "cb-badge-info",
  backfilling: "cb-badge-progress",
  watching: "cb-badge-success",
  validating: "cb-badge-progress",
  validation_failed: "cb-badge-error",
  failed: "cb-badge-error",
  stopped: "cb-badge-warning",
  paused: "cb-badge-warning",
  draft: "cb-badge-info",
};

const PHASE_LABEL: Record<string, string> = {
  ready: "Ready",
  backfilling: "Backfilling",
  watching: "Watching for new documents",
  validating: "Validating",
  validation_failed: "Validation failed",
  failed: "Failed",
  stopped: "Stopped",
  paused: "Paused",
  draft: "Draft",
};

export default function DashboardPage() {
  const [jobs, setJobs] = useState<any[]>([]);
  const { data: liveUpdate } = useVectorizerSocket("*");

  useEffect(() => {
    getDashboardStats().then((d) => setJobs(d.jobs || [])).catch(() => {});
  }, []);

  useEffect(() => {
    if (!liveUpdate) return;
    setJobs((prev) => {
      const idx = prev.findIndex((j) => j.job_id === (liveUpdate as any).job_id);
      if (idx === -1) return [liveUpdate, ...prev];
      const copy = [...prev];
      copy[idx] = liveUpdate;
      return copy;
    });
  }, [liveUpdate]);

  const agg = jobs.reduce(
    (acc, j) => {
      acc.serverConnections = Math.max(acc.serverConnections, j.stats?.server_connections || 0);
      acc.buckets += j.stats?.bucket_count || 0;
      acc.docsTotal += j.stats?.docs_total || 0;
      acc.docsVectorized += j.stats?.docs_vectorized || 0;
      acc.docsInProgress += j.stats?.docs_in_progress || 0;
      acc.docsPerMinute += j.stats?.docs_per_minute || 0;
      return acc;
    },
    { serverConnections: 0, buckets: 0, docsTotal: 0, docsVectorized: 0, docsInProgress: 0, docsPerMinute: 0 }
  );

  async function handleStop(jobId: string) {
    await stopJob(jobId).catch(() => {});
  }

  return (
    <div style={{ padding: 32 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 24 }}>
        <div>
          <h1 style={{ fontSize: 20, marginBottom: 4 }}>Vectorizer Dashboard</h1>
          <p style={{ color: "var(--text-secondary)", fontSize: 13 }}>
            Real-time JSON document vector embeddings, backed by Couchbase Vector Search.
          </p>
        </div>
        <Link to="/setup" className="cb-btn cb-btn-primary">New Agent</Link>
      </div>

      <div style={{ display: "flex", gap: 14, marginBottom: 20, flexWrap: "wrap" }}>
        <StatCard label="Server connections" value={String(agg.serverConnections)} />
        <StatCard label="Buckets" value={String(agg.buckets)} />
        <StatCard label="Total JSON documents" value={agg.docsTotal.toLocaleString()} />
        <StatCard label="Documents vectorized" value={agg.docsVectorized.toLocaleString()} accent="var(--status-success)" />
        <StatCard label="Vectorized / min" value={agg.docsPerMinute.toFixed(1)} accent="var(--cb-teal)" />
        <StatCard label="Embeddings in progress" value={String(agg.docsInProgress)} accent="var(--cb-amber)" />
      </div>

      <div style={{ marginBottom: 24 }}>
        <VectorizationRateChart docsPerMinute={agg.docsPerMinute} />
      </div>

      <div className="cb-card" style={{ overflow: "hidden" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
          <thead>
            <tr style={{ textAlign: "left", background: "var(--bg-2)" }}>
              {["Name", "Embedding model", "Buckets", "Phase", "Progress", ""].map((h) => (
                <th key={h} style={{ padding: "10px 14px", fontSize: 11, color: "var(--text-muted)", fontWeight: 700, textTransform: "uppercase" }}>
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {jobs.length === 0 && (
              <tr><td colSpan={6} style={{ padding: 24, textAlign: "center", color: "var(--text-muted)" }}>
                No vectorizer agents yet. Create one to get started.
              </td></tr>
            )}
            {jobs.map((j) => {
              const total = j.stats?.docs_total || 0;
              const done = j.stats?.docs_vectorized || 0;
              const pct = total ? Math.min(100, (done / total) * 100) : 0;
              const active = ["backfilling", "watching"].includes(j.phase);
              return (
                <tr key={j.job_id} style={{ borderTop: "1px solid var(--border-subtle)" }}>
                  <td style={{ padding: "10px 14px" }}>{j.plan?.name}</td>
                  <td style={{ padding: "10px 14px", color: "var(--text-secondary)" }}>{j.plan?.embedding_model}</td>
                  <td style={{ padding: "10px 14px", color: "var(--text-secondary)" }}>
                    {(j.plan?.source?.bucket_names || []).join(", ")}
                  </td>
                  <td style={{ padding: "10px 14px" }}>
                    <span className={`cb-badge ${PHASE_BADGE[j.phase] || "cb-badge-info"}`}>
                      {PHASE_LABEL[j.phase] || j.phase}
                    </span>
                  </td>
                  <td style={{ padding: "10px 14px", width: 160 }}>
                    <div style={{ background: "var(--bg-3)", borderRadius: 999, height: 6, overflow: "hidden" }}>
                      <div style={{ width: `${pct}%`, height: "100%", background: "var(--cb-teal)" }} />
                    </div>
                    <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 3 }}>
                      {done.toLocaleString()} / {total.toLocaleString()}
                    </div>
                  </td>
                  <td style={{ padding: "10px 14px" }}>
                    {active && (
                      <button className="cb-btn cb-btn-danger" style={{ padding: "4px 10px" }} onClick={() => handleStop(j.job_id)}>
                        Stop
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
