import { CheckCircle2, XCircle, AlertTriangle, Info, Activity } from "lucide-react";

export interface OperationEntry {
  id: string;
  timestamp: string;
  status: "info" | "success" | "warning" | "error";
  bucket?: string | null;
  doc_id?: string | null;
  message: string;
}

const ICONS: Record<string, JSX.Element> = {
  success: <CheckCircle2 size={14} color="var(--status-success)" />,
  error: <XCircle size={14} color="var(--status-error)" />,
  warning: <AlertTriangle size={14} color="var(--status-warning)" />,
  info: <Info size={14} color="var(--status-info)" />,
};

/** Persistent right-rail live feed of vectorizer operations, streamed over the
 * /ws/jobs websocket. Kept as a fixed column (rather than a toggle-open panel)
 * since it's meant to be glanced at continuously while the agent runs. */
export default function OperationsFeed({ ops }: { ops: OperationEntry[] }) {
  return (
    <aside
      style={{
        width: 320,
        flexShrink: 0,
        borderLeft: "1px solid var(--border-subtle)",
        background: "var(--bg-1)",
        display: "flex",
        flexDirection: "column",
        height: "100vh",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          padding: "16px 16px 12px",
          borderBottom: "1px solid var(--border-subtle)",
        }}
      >
        <Activity size={15} color="var(--cb-teal)" />
        <span style={{ fontWeight: 700, fontSize: 13 }}>Agent Operations</span>
      </div>

      <div style={{ flex: 1, overflow: "auto", padding: 12, display: "flex", flexDirection: "column", gap: 8 }} className="cb-scrollbar">
        {ops.length === 0 && (
          <div style={{ color: "var(--text-muted)", fontSize: 12, padding: "12px 4px" }}>
            No operations yet. Launch a job to see live vectorization activity here.
          </div>
        )}
        {ops.map((op) => (
          <div
            key={op.id}
            className="cb-card"
            style={{ padding: "8px 10px", display: "flex", gap: 8, alignItems: "flex-start" }}
          >
            <div style={{ marginTop: 2, flexShrink: 0 }}>{ICONS[op.status] ?? ICONS.info}</div>
            <div style={{ minWidth: 0 }}>
              <div style={{ fontSize: 12, lineHeight: 1.4, wordBreak: "break-word" }}>{op.message}</div>
              <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 3, display: "flex", gap: 6, flexWrap: "wrap" }}>
                {op.bucket && <span>bucket: {op.bucket}</span>}
                {op.doc_id && <span style={{ fontFamily: "var(--font-mono)" }}>{op.doc_id}</span>}
                <span>{new Date(op.timestamp).toLocaleTimeString()}</span>
              </div>
            </div>
          </div>
        ))}
      </div>
    </aside>
  );
}
