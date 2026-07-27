import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { listMigrations } from "@/api/client";
import { useMigrationSocket } from "@/hooks/useMigrationSocket";
import StatCard from "@/components/dashboard/StatCard";

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

export default function DashboardPage() {
  const [migrations, setMigrations] = useState<any[]>([]);
  const { data: liveUpdate } = useMigrationSocket("*");

  useEffect(() => {
    listMigrations().then(setMigrations).catch(() => {});
  }, []);

  useEffect(() => {
    if (!liveUpdate) return;
    setMigrations((prev) => {
      const idx = prev.findIndex((m) => m.migration_id === (liveUpdate as any).migration_id);
      if (idx === -1) return [liveUpdate, ...prev];
      const copy = [...prev];
      copy[idx] = liveUpdate;
      return copy;
    });
  }, [liveUpdate]);

  const active = migrations.filter((m) => !["complete", "failed", "rolled_back", "cancelled"].includes(m.phase)).length;
  const complete = migrations.filter((m) => m.phase === "complete").length;
  const totalDocs = migrations.reduce((sum, m) => sum + (m.stats?.docs_migrated || 0), 0);

  return (
    <div style={{ padding: 32 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 24 }}>
        <div>
          <h1 style={{ fontSize: 20, marginBottom: 4 }}>Migrations</h1>
          <p style={{ color: "var(--text-secondary)", fontSize: 13 }}>
            Couchbase Server → Couchbase Capella migration jobs
          </p>
        </div>
        <Link to="/new" className="cb-btn cb-btn-primary">New Migration</Link>
      </div>

      <div style={{ display: "flex", gap: 14, marginBottom: 28 }}>
        <StatCard label="Active migrations" value={String(active)} accent="var(--cb-teal)" />
        <StatCard label="Completed" value={String(complete)} accent="var(--status-success)" />
        <StatCard label="Documents migrated" value={totalDocs.toLocaleString()} />
        <StatCard label="Total jobs" value={String(migrations.length)} />
      </div>

      <div className="cb-card" style={{ overflow: "hidden" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
          <thead>
            <tr style={{ textAlign: "left", background: "var(--bg-2)" }}>
              {["Name", "Source", "Destination", "Phase", "Progress", ""].map((h) => (
                <th key={h} style={{ padding: "10px 14px", fontSize: 11, color: "var(--text-muted)", fontWeight: 700, textTransform: "uppercase" }}>
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {migrations.length === 0 && (
              <tr><td colSpan={6} style={{ padding: 24, textAlign: "center", color: "var(--text-muted)" }}>
                No migrations yet. Create one to get started.
              </td></tr>
            )}
            {migrations.map((m) => {
              const pct = m.stats?.docs_total ? Math.min(100, (m.stats.docs_migrated / m.stats.docs_total) * 100) : 0;
              return (
                <tr key={m.migration_id} style={{ borderTop: "1px solid var(--border-subtle)" }}>
                  <td style={{ padding: "10px 14px" }}>
                    <Link to={`/migrations/${m.migration_id}`}>{m.plan?.name}</Link>
                  </td>
                  <td style={{ padding: "10px 14px", color: "var(--text-secondary)" }}>{m.plan?.source?.label}</td>
                  <td style={{ padding: "10px 14px", color: "var(--text-secondary)" }}>{m.plan?.destination?.label}</td>
                  <td style={{ padding: "10px 14px" }}>
                    <span className={`cb-badge ${PHASE_BADGE[m.phase] || "cb-badge-info"}`}>{m.phase}</span>
                  </td>
                  <td style={{ padding: "10px 14px", width: 160 }}>
                    <div style={{ background: "var(--bg-3)", borderRadius: 999, height: 6, overflow: "hidden" }}>
                      <div style={{ width: `${pct}%`, height: "100%", background: "var(--cb-teal)" }} />
                    </div>
                  </td>
                  <td style={{ padding: "10px 14px" }}>
                    <Link to={`/migrations/${m.migration_id}`} className="cb-btn" style={{ padding: "4px 10px" }}>
                      View
                    </Link>
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
