import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useWizardStore } from "@/store/wizardStore";
import StepIndicator from "@/components/wizard/StepIndicator";
import ClusterConfigForm from "@/components/wizard/ClusterConfigForm";
import ValidationResults from "@/components/validation/ValidationResults";
import ClusterTopologyDiagram from "@/components/topology/ClusterTopologyDiagram";
import ReplicationModeSelector from "@/components/wizard/ReplicationModeSelector";
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
    // "Continue" button below is gated on backupResult.status === "complete"
    // instead, and a failed run's error_message is shown right here so users
    // don't have to go dig through container logs to find out what happened.
    await guarded(async () => {
      const record: any = await backupMigration(wizard.migrationId!);
      setBackupResult(record.backup_record ?? record);
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
          nextDisabled={backupResult?.status !== "complete"}
          nextLabel={
            backupResult?.status === "complete"
              ? "Continue"
              : backupResult?.status === "failed"
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
            <button className="cb-btn" onClick={handleBackup} disabled={busy}>
              {busy
                ? "Backing up…"
                : backupResult?.status === "failed"
                ? "Retry backup (cbbackupmgr)"
                : "Run backup (cbbackupmgr)"}
            </button>
            {backupResult?.status === "complete" && (
              <span className="cb-badge cb-badge-success" style={{ marginLeft: 10 }}>Complete</span>
            )}
            {backupResult?.status === "failed" && (
              <span className="cb-badge cb-badge-error" style={{ marginLeft: 10 }}>Failed</span>
            )}
          </div>
          {backupResult && (
            <div className="cb-card" style={{ padding: 12, marginTop: 14, maxWidth: 640 }}>
              <div style={{ fontSize: 12 }}>Backup archive: {backupResult.archive_path}</div>
              <div style={{ fontSize: 12, marginTop: 4 }}>Status: {backupResult.status}</div>
              {backupResult.status === "failed" && backupResult.error_message && (
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
                  {backupResult.error_message}
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
            <div><b>Backup:</b> {backupResult?.status ?? "pending"}</div>
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
