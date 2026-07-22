export default function StatCard({ label, value, sub, accent }: { label: string; value: string; sub?: string; accent?: string }) {
  return (
    <div className="cb-card" style={{ padding: "16px 18px", flex: 1, minWidth: 150 }}>
      <div style={{ fontSize: 11, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.04em", fontWeight: 700 }}>
        {label}
      </div>
      <div style={{ fontSize: 24, fontWeight: 700, marginTop: 6, color: accent || "var(--text-primary)" }}>
        {value}
      </div>
      {sub && <div style={{ fontSize: 11, color: "var(--text-secondary)", marginTop: 4 }}>{sub}</div>}
    </div>
  );
}
