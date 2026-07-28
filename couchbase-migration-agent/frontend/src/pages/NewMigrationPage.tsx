import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useWizardStore } from "@/store/wizardStore";
import StepIndicator from "@/components/wizard/StepIndicator";
import ClusterConfigForm from "@/components/wizard/ClusterConfigForm";
import ValidationResults from "@/components/validation/ValidationResults";
import ClusterTopologyDiagram from "@/components/topology/ClusterTopologyDiagram";
import ReplicationModeSelector from "@/components/wizard/ReplicationModeSelector";
import { useMigrationSocket } from "@/hooks/useMigrationSocket";
import {
  testConnection,
  createMigration,
  validateMigration,
  backupMigration,
  approveMigration,
} from "@/api/client";

const STRATEGY_LABELS: Record<string, string> = {
  backup_restore: "One-time migration",
  xdcr_live: "Continuous replication",
  hybrid: "Bulk copy + continuous sync",
};

/** Renders a Go-style duration (seconds) the way the rest of the app phrases ETAs. */
function formatEta(seconds?: number | null): string {
  if (seconds == null || !isFinite(seconds) || seconds < 0) return "—";
  const s = Math.round(seconds);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (h > 0) return `about ${h}h${m}m remaining`;
  if (m > 0) return `about ${m}m${sec}s remaining`;
  return `about ${sec}s remaining`;
}

export default function NewMigrationPage() {
  const wizard = useWizardStore();
  const navigate = useNavigate();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sourceTopo, setSourceTopo] = useState<any>(null);
  const [destTopo, setDestTopo] = useState<any>(null);
  const [validation, setValidation] = useState<any>(null);
  const [backupResult, setBackupResult] = useState<any>(null);
  const isContinuousStrategy = wizard.strategy === "xdcr_live" || wizard.strategy === "hybrid";

  // Live progress during the backup step: backupMigration() below blocks on the HTTP
  // response for the whole backup duration, so the only way to show a moving
  // progress bar/ETA while it runs is to also listen on the same per-migration
  // websocket channel the migration detail page uses for restore progress --
  // MigrationEngine.backup_source() now pushes a broadcast on every parsed
  // cbbackupmgr progress tick, not just at completion (see backup_manager.py's
  // BackupManager.backup()/_run_streaming()).
  const { data: liveRecord } = useMigrationSocket(wizard.migrationId || "none");
  const liveBackup =
    liveRecord && wizard.migrationId && (liveRecord as any).migration_id === wizard.migrationId
      ? (liveRecord as any).backup_record
      : null;
  // Prefer the live websocket snapshot while it's tracking the current migration;
  // fall back to the final result from the HTTP response (e.g. right after page
  // load, before any socket frame has arrived yet, or if the socket briefly drops).
  const displayedBackup = liveBackup ?? backupResult;

  async function guarded(fn: () => Promise<void>) {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (e: any) {
      setError(e.message || String(e));
    } finally {
      setBusy(false);
    }
  }

  async function handleTestSource() {
    await guarded(async () => setSourceTopo(await testConnection(wizard.source)));
  }
  async function handleTestDestination() {
    await guarded(async () => setDestTopo(await testConnection(wizard.destination)));
  }

  async function handleCreateAndValidate() {
    await guarded(async () => {
      // POST /api/migrations always mints a brand new migration_id -- if the user
      // ends up back here and re-creates/re-validates (e.g. after backing up to fix
      // something on step 4 and returning to step 1), the previous migration_id's
      // backupResult would otherwise keep showing on screen as if it belonged to
      // this new record, even though the new record has never actually been backed
      // up. Clear it here so the Backup step can't display a stale "complete" (or
      // "failed") card for a migration_id that no longer matches wizard.migrationId.
      setBackupResult(null);
      const plan = {
        name: wizard.migrationName || "Untitled migration",
        source: wizard.source,
        destination: wizard.destination,
        strategy: wizard.strategy,
        buckets: (sourceTopo?.buckets || []).map((b: string) => ({ bucket_name: b, include: true })),
      };
      const record: any = await createMigration(plan);
      wizard.setMigrationId(record.migration_id);
      const report = await validateMigration(record.migration_id);
      setValidation(report);
      wizard.setStep(2);
    });
  }

  async function handleBackup() {
    // Deliberately doesn't advance the wizard step here, even on success --
    // backupMigration() resolves with HTTP 200 whether the backup succeeded or
    // failed (a failed backup is a normal domain outcome, not a request error),
    // so advancing unconditionally used to silently carry the user past a
    // failed backup into the Approve step with no visible reason why. The
    // "Continue" button below is gated on displayedBackup.status === "complete"
    // instead, and a failed run's error_message is shown right here so users
    // don't have to go dig through container logs to find out what happened.
    //
    // backupMigration() now only *schedules* the backup server-side and returns
    // right away (see the backend route) -- it used to block for the whole backup
    // duration (a minute-plus against a real cluster), and holding one HTTP request
    // open that long was exposed to any idle-connection reset along the way,
    // surfacing as a confusing "NetworkError" even when the backup itself finished
    // fine. Progress and the final result now come entirely from the websocket (see
    // displayedBackup above), which isn't tied to this request's lifetime at all --
    // this call's response is intentionally unused here.
    await guarded(async () => {
      await backupMigration(wizard.migrationId!);
    });
  }

  async function handleApprove() {
    await guarded(async () => {
      await approveMigration(wizard.migrationId!, "erikhinderer@gmail.com");
      navigate(`/migrations/${wizard.migrationId}`);
    });
  }

  return (
    <div style={{ padding: 32, maxWidth: 960 }}>
      <h1 style={{ fontSize: 20, marginBottom: 4 }}>New Migration</h1>
      <p style={{ color: "var(--text-secondary)", fontSize: 13, marginBottom: 28 }}>
        Connect source and destination clusters, validate compatibility, back up the
        source, then approve to start the migration to Capella.
      </p>

      <StepIndicator step={wizard.step} />

      {error && (
        <div className="cb-card" style={{ padding: 12, marginBottom: 16, borderColor: "var(--status-error)" }}>
          <span style={{ color: "var(--status-error)", fontSize: 13 }}>{error}</span>
        </div>
      )}

      {wizard.step === 0 && (
        <StepShell
          title="Migration name & source cluster"
          onNext={() => wizard.setStep(1)}
          nextDisabled={!sourceTopo}
        >
          <input
            placeholder="Migration name (e.g. prod-cluster-to-capella)"
            value={wizard.migrationName}
            onChange={(e) => wizard.setMigrationName(e.target.value)}
            style={{ maxWidth: 480, marginBottom: 18 }}
          />
          <ClusterConfigForm value={wizard.source} onChange={wizard.updateSource} disableCapellaToggle />
          <div style={{ marginTop: 16 }}>
            <button className="cb-btn" onClick={handleTestSource} disabled={busy}>
              Test & introspect source
            </button>
            {sourceTopo && (
              <span className="cb-badge cb-badge-success" style={{ marginLeft: 10 }}>
                Connected · {sourceTopo.buckets.length} buckets · v{sourceTopo.cluster_version}
              </span>
            )}
          </div>
        </StepShell>
      )}

      {wizard.step === 1 && (
        <StepShell title="Destination (Capella) cluster" onBack={() => wizard.setStep(0)} onNext={handleCreateAndValidate} nextDisabled={!destTopo || busy} nextLabel="Create & validate">
          <ClusterConfigForm value={wizard.destination} onChange={wizard.updateDestination} />
          <div style={{ marginTop: 16, marginBottom: 28 }}>
            <button className="cb-btn" onClick={handleTestDestination} disabled={busy}>
              Test destination connection
            </button>
            {destTopo && (
              <span className="cb-badge cb-badge-success" style={{ marginLeft: 10 }}>
                Reachable
              </span>
            )}
          </div>

          <h3 style={{ fontSize: 13, marginBottom: 4 }}>Replication mode</h3>
          <p style={{ fontSize: 12, color: "var(--text-muted)", marginBottom: 14, maxWidth: 640 }}>
            How should data move from source to destination? You can stop or cut over a
            continuous replication at any time from the migration detail page.
          </p>
          <ReplicationModeSelector value={wizard.strategy} onChange={wizard.setStrategy} />
        </StepShell>
      )}

      {wizard.step === 2 && (
        <StepShell
          title="Validation results"
          onBack={() => wizard.setStep(1)}
          onNext={() => wizard.setStep(3)}
          nextDisabled={!validation?.passed}
          nextLabel={validation?.passed ? "Continue" : "Resolve errors to continue"}
        >
          <div style={{ marginBottom: 20 }}>
            <ClusterTopologyDiagram
              source={sourceTopo && {
                label: wizard.source.label,
                version: sourceTopo.cluster_version,
                nodes: sourceTopo.nodes,
                buckets: sourceTopo.buckets,
                // Only show XDCR remotes when this migration is actually using a
                // continuous (XDCR-based) strategy -- the source cluster may already
                // have unrelated XDCR replications configured (e.g. from a previous
                // migration attempt against this same real cluster), and showing that
                // satellite node here implied this migration involved XDCR even when
                // "One-time migration" was selected.
                xdcrRemotes: isContinuousStrategy ? sourceTopo.xdcr_remotes : undefined,
              }}
              destination={destTopo && { label: wizard.destination.label, isCapella: true, nodes: destTopo.nodes, buckets: destTopo.buckets }}
              phase="validated"
            />
          </div>
          <ValidationResults checks={validation?.checks || []} />
        </StepShell>
      )}

      {wizard.step === 3 && (
        <StepShell
          title="Back up source cluster"
          onBack={() => wizard.setStep(2)}
          onNext={() => wizard.setStep(4)}
          nextDisabled={displayedBackup?.status !== "complete"}
          nextLabel={
            displayedBackup?.status === "complete"
              ? "Continue"
              : displayedBackup?.status === "failed"
              ? "Resolve backup error to continue"
              : "Run backup to continue"
          }
        >
          <p style={{ fontSize: 13, color: "var(--text-secondary)", maxWidth: 560 }}>
            A full backup of the source cluster is taken before any data is transferred.
            If the migration fails, or you cancel it, the source is rolled back to this
            exact backup — the source cluster is never left in a partially-migrated state.
          </p>
          <div style={{ marginTop: 16 }}>
            {/* backupMigration() now only schedules the backup and returns almost
                instantly (see handleBackup()'s comment), so `busy` alone would only
                disable this button for a moment -- also disable/label off the
                websocket-driven displayedBackup.status so a still-running backup
                can't be started twice from a second click. */}
            <button
              className="cb-btn"
              onClick={handleBackup}
              disabled={busy || displayedBackup?.status === "running"}
            >
              {busy || displayedBackup?.status === "running"
                ? "Backing up…"
                : displayedBackup?.status === "failed"
                ? "Retry backup (cbbackupmgr)"
                : "Run backup (cbbackupmgr)"}
            </button>
            {displayedBackup?.status === "complete" && (
              <span className="cb-badge cb-badge-success" style={{ marginLeft: 10 }}>Complete</span>
            )}
            {displayedBackup?.status === "failed" && (
              <span className="cb-badge cb-badge-error" style={{ marginLeft: 10 }}>Failed</span>
            )}
            {displayedBackup?.status === "running" && (
              <span className="cb-badge cb-badge-progress" style={{ marginLeft: 10 }}>Running</span>
            )}
          </div>

          {displayedBackup?.status === "running" && (
            <div style={{ marginTop: 16, maxWidth: 560 }}>
              <div
                style={{
                  height: 8, borderRadius: 4, background: "var(--bg-3)",
                  border: "1px solid var(--border-subtle)", overflow: "hidden",
                }}
              >
                <div
                  style={{
                    height: "100%",
                    width: `${Math.min(Math.max(displayedBackup.progress_pct ?? 0, 0), 100)}%`,
                    background: "var(--status-progress)",
                    transition: "width 0.3s ease",
                  }}
                />
              </div>
              <div
                style={{
                  display: "flex", justifyContent: "space-between",
                  fontSize: 11, color: "var(--text-muted)", marginTop: 6,
                }}
              >
                <span>
                  {(displayedBackup.progress_pct ?? 0).toFixed(1)}%
                  {displayedBackup.docs_done ? ` · ${displayedBackup.docs_done.toLocaleString()} items` : ""}
                  {displayedBackup.size_mb_done ? ` · ${displayedBackup.size_mb_done.toFixed(1)} MiB` : ""}
                  {displayedBackup.throughput_mb_per_sec
                    ? ` · ${displayedBackup.throughput_mb_per_sec.toFixed(1)} MiB/s`
                    : ""}
                </span>
                <span>{formatEta(displayedBackup.eta_seconds)}</span>
              </div>
            </div>
          )}

          {displayedBackup && (
            <div className="cb-card" style={{ padding: 12, marginTop: 14, maxWidth: 640 }}>
              <div style={{ fontSize: 12 }}>Backup archive: {displayedBackup.archive_path}</div>
              <div style={{ fontSize: 12, marginTop: 4 }}>Status: {displayedBackup.status}</div>
              {displayedBackup.status === "failed" && displayedBackup.error_message && (
                <pre
                  style={{
                    fontSize: 11,
                    marginTop: 10,
                    padding: 10,
                    whiteSpace: "pre-wrap",
                    wordBreak: "break-word",
                    color: "var(--status-error)",
                    background: "rgba(255,75,79,0.08)",
                    border: "1px solid rgba(255,75,79,0.25)",
                    borderRadius: 4,
                    maxHeight: 240,
                    overflowY: "auto",
                  }}
                >
                  {displayedBackup.error_message}
                </pre>
              )}
            </div>
          )}
        </StepShell>
      )}

      {wizard.step === 4 && (
        <StepShell title="Review & approve" onBack={() => wizard.setStep(3)} onNext={handleApprove} nextDisabled={busy} nextLabel="Approve & start migration">
          <div className="cb-card" style={{ padding: 16, maxWidth: 560, fontSize: 13, lineHeight: 1.8 }}>
            <div><b>Migration:</b> {wizard.migrationName}</div>
            <div><b>Source:</b> {wizard.source.label} ({wizard.source.connection_string})</div>
            <div><b>Destination:</b> {wizard.destination.label} ({wizard.destination.connection_string})</div>
            <div><b>Replication mode:</b> {STRATEGY_LABELS[wizard.strategy]}</div>
            <div><b>Backup:</b> {displayedBackup?.status ?? "pending"}</div>
          </div>
          <p style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 14, maxWidth: 560 }}>
            {wizard.strategy === "backup_restore"
              ? "Approving starts the one-time transfer to the destination. You can trigger " +
                "a rollback to the pre-migration backup at any point from the migration detail page."
              : "Approving starts continuous replication to the destination immediately and " +
                "leaves it running. From the migration detail page you can stop it at any time " +
                "— either cut over (destination becomes authoritative) or halt without cutover " +
                "— or roll the source back to the pre-migration backup."}
          </p>
        </StepShell>
      )}
    </div>
  );
}

function StepShell({
  title, children, onBack, onNext, nextDisabled, nextLabel,
}: {
  title: string; children: React.ReactNode; onBack?: () => void; onNext: () => void;
  nextDisabled?: boolean; nextLabel?: string;
}) {
  return (
    <div>
      <h2 style={{ fontSize: 15, marginBottom: 16 }}>{title}</h2>
      {children}
      <div style={{ display: "flex", gap: 10, marginTop: 24 }}>
        {onBack && <button className="cb-btn" onClick={onBack}>Back</button>}
        <button className="cb-btn cb-btn-primary" onClick={onNext} disabled={nextDisabled}>
          {nextLabel ?? "Next"}
        </button>
      </div>
    </div>
  );
}
