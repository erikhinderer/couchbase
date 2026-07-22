import { CheckCircle2, AlertTriangle, XCircle, Info } from "lucide-react";

interface Check {
  check_id: string;
  label: string;
  severity: "info" | "warning" | "error";
  passed: boolean;
  message: string;
}

export default function ValidationResults({ checks }: { checks: Check[] }) {
  if (!checks?.length) return null;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      {checks.map((c, i) => (
        <div
          key={`${c.check_id}-${i}`}
          className="cb-card"
          style={{ display: "flex", gap: 10, padding: "10px 14px", alignItems: "flex-start" }}
        >
          <Icon passed={c.passed} severity={c.severity} />
          <div>
            <div style={{ fontSize: 13, fontWeight: 600 }}>{c.label}</div>
            <div style={{ fontSize: 12, color: "var(--text-secondary)", marginTop: 2 }}>{c.message}</div>
          </div>
        </div>
      ))}
    </div>
  );
}

function Icon({ passed, severity }: { passed: boolean; severity: string }) {
  if (passed) return <CheckCircle2 size={18} color="var(--status-success)" style={{ flexShrink: 0, marginTop: 1 }} />;
  if (severity === "error") return <XCircle size={18} color="var(--status-error)" style={{ flexShrink: 0, marginTop: 1 }} />;
  if (severity === "warning") return <AlertTriangle size={18} color="var(--status-warning)" style={{ flexShrink: 0, marginTop: 1 }} />;
  return <Info size={18} color="var(--status-info)" style={{ flexShrink: 0, marginTop: 1 }} />;
}
