const STEPS = ["Source", "Destination", "Embedding Model", "Launch"];

export default function StepIndicator({ step }: { step: number }) {
  return (
    <div style={{ display: "flex", gap: 0, marginBottom: 28 }}>
      {STEPS.map((label, i) => (
        <div key={label} style={{ display: "flex", alignItems: "center", flex: i < STEPS.length - 1 ? 1 : "unset" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <div
              style={{
                width: 26, height: 26, borderRadius: "50%",
                display: "flex", alignItems: "center", justifyContent: "center",
                fontSize: 12, fontWeight: 700,
                background: i <= step ? "var(--cb-red)" : "var(--bg-3)",
                color: i <= step ? "white" : "var(--text-muted)",
                border: i === step ? "2px solid var(--cb-red-bright)" : "none",
              }}
            >
              {i + 1}
            </div>
            <span style={{ fontSize: 12, fontWeight: 600, color: i <= step ? "var(--text-primary)" : "var(--text-muted)" }}>
              {label}
            </span>
          </div>
          {i < STEPS.length - 1 && (
            <div style={{ flex: 1, height: 2, background: i < step ? "var(--cb-red)" : "var(--border-subtle)", margin: "0 12px" }} />
          )}
        </div>
      ))}
    </div>
  );
}
