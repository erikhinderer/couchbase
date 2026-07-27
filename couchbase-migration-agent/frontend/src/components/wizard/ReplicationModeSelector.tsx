import { CheckCircle2 } from "lucide-react";
import type { WizardStrategy } from "@/store/wizardStore";

const OPTIONS: { value: WizardStrategy; title: string; description: string }[] = [
  {
    value: "backup_restore",
    title: "One-time migration",
    description:
      "A single cbbackupmgr snapshot of the source is restored to the destination. " +
      "The migration finishes once the restore completes — nothing keeps syncing " +
      "afterward. Best for a scheduled cutover window.",
  },
  {
    value: "xdcr_live",
    title: "Continuous replication",
    description:
      "Cross Data Center Replication (XDCR) streams changes from source to " +
      "destination continuously, indefinitely, starting immediately after approval. " +
      "Stop it anytime — either cut over (destination becomes authoritative) or halt " +
      "without cutover.",
  },
  {
    value: "hybrid",
    title: "Bulk copy + continuous sync",
    description:
      "A one-time backup/restore moves existing data first, then XDCR takes over for " +
      "ongoing delta sync. Combines a faster initial bulk load with continuous " +
      "replication for everything written afterward.",
  },
];

export default function ReplicationModeSelector({
  value,
  onChange,
}: {
  value: WizardStrategy;
  onChange: (v: WizardStrategy) => void;
}) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10, maxWidth: 640 }}>
      {OPTIONS.map((opt) => {
        const selected = value === opt.value;
        return (
          <button
            key={opt.value}
            type="button"
            onClick={() => onChange(opt.value)}
            className="cb-card"
            style={{
              textAlign: "left",
              padding: "14px 16px",
              cursor: "pointer",
              borderColor: selected ? "var(--cb-red)" : "var(--border-subtle)",
              background: selected ? "rgba(234,35,40,0.06)" : "var(--bg-1)",
              display: "flex",
              gap: 12,
              alignItems: "flex-start",
            }}
          >
            <div
              style={{
                width: 18, height: 18, borderRadius: "50%", flexShrink: 0, marginTop: 2,
                border: `2px solid ${selected ? "var(--cb-red)" : "var(--border-strong)"}`,
                display: "flex", alignItems: "center", justifyContent: "center",
              }}
            >
              {selected && <CheckCircle2 size={14} color="var(--cb-red)" fill="var(--cb-red)" />}
            </div>
            <div>
              <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 4 }}>
                {opt.title}
                {opt.value !== "backup_restore" && (
                  <span className="cb-badge cb-badge-progress" style={{ marginLeft: 8 }}>
                    Continuous
                  </span>
                )}
              </div>
              <div style={{ fontSize: 12, color: "var(--text-secondary)", lineHeight: 1.5 }}>
                {opt.description}
              </div>
            </div>
          </button>
        );
      })}
    </div>
  );
}
