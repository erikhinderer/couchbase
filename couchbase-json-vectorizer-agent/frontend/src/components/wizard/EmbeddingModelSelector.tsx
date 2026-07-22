import { CheckCircle2 } from "lucide-react";

export interface EmbeddingModelInfo {
  model_id: string;
  display_name: string;
  dimensions: number;
  popularity_rank: number;
  approx_downloads: string;
  description: string;
  languages: string;
  approx_size_mb: number;
}

export default function EmbeddingModelSelector({
  models,
  value,
  onChange,
}: {
  models: EmbeddingModelInfo[];
  value: string;
  onChange: (modelId: string) => void;
}) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10, maxWidth: 700 }}>
      {models
        .slice()
        .sort((a, b) => a.popularity_rank - b.popularity_rank)
        .map((m) => {
          const selected = value === m.model_id;
          return (
            <button
              key={m.model_id}
              type="button"
              onClick={() => onChange(m.model_id)}
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
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 4, display: "flex", alignItems: "center", gap: 8 }}>
                  #{m.popularity_rank} {m.display_name}
                  <span className="cb-badge cb-badge-info">{m.dimensions} dims</span>
                  <span style={{ fontSize: 11, color: "var(--text-muted)", fontWeight: 500 }}>{m.approx_downloads} downloads</span>
                </div>
                <div style={{ fontSize: 12, color: "var(--text-secondary)", lineHeight: 1.5, marginBottom: 4 }}>
                  {m.description}
                </div>
                <div style={{ fontSize: 11, color: "var(--text-muted)" }}>
                  {m.languages} · ~{m.approx_size_mb}MB · {m.model_id}
                </div>
              </div>
            </button>
          );
        })}
    </div>
  );
}
