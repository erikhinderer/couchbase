import { useState } from "react";
import { useParams } from "react-router-dom";
import { MessageSquareText, Send, X } from "lucide-react";
import { api, ApiError } from "@/api/client";

interface ChatTurn {
  role: "user" | "assistant";
  content: string;
}

export default function AgentPanel() {
  const { id: migrationId } = useParams();
  const [open, setOpen] = useState(false);
  const [message, setMessage] = useState("");
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [sending, setSending] = useState(false);

  async function send() {
    const text = message.trim();
    if (!text || sending) return;
    setTurns((prev) => [...prev, { role: "user", content: text }]);
    setMessage("");
    setSending(true);
    try {
      const resp = await api.chat(text, migrationId);
      setTurns((prev) => [...prev, { role: "assistant", content: resp.reply }]);
    } catch (err) {
      const detail = err instanceof ApiError ? err.message : "Something went wrong reaching the agent.";
      setTurns((prev) => [...prev, { role: "assistant", content: detail }]);
    } finally {
      setSending(false);
    }
  }

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="cb-btn cb-btn-primary"
        style={{
          position: "fixed", bottom: 24, right: 24, borderRadius: 999,
          width: 52, height: 52, display: "flex", alignItems: "center", justifyContent: "center",
        }}
        aria-label="Ask the agent"
      >
        <MessageSquareText size={20} />
      </button>
    );
  }

  return (
    <div
      style={{
        position: "fixed", bottom: 24, right: 24, width: 360, height: 480,
        background: "var(--bg-1)", border: "1px solid var(--border-subtle)",
        borderRadius: "var(--radius-md)", display: "flex", flexDirection: "column", overflow: "hidden",
        boxShadow: "0 12px 40px rgba(0,0,0,0.5)",
      }}
    >
      <div
        style={{
          padding: "12px 14px", borderBottom: "1px solid var(--border-subtle)",
          display: "flex", alignItems: "center", justifyContent: "space-between",
        }}
      >
        <div style={{ fontSize: 13, fontWeight: 700 }}>Ask the agent</div>
        <button onClick={() => setOpen(false)} style={{ background: "none", border: "none", color: "var(--text-muted)" }}>
          <X size={16} />
        </button>
      </div>
      <div className="cb-scrollbar" style={{ flex: 1, overflowY: "auto", padding: 14, display: "flex", flexDirection: "column", gap: 10 }}>
        {turns.length === 0 && (
          <div style={{ fontSize: 12.5, color: "var(--text-muted)" }}>
            Ask about validation failures, migration strategy, or past incidents -- the agent recalls similar
            events from its Couchbase-backed memory.
          </div>
        )}
        {turns.map((t, i) => (
          <div
            key={i}
            style={{
              alignSelf: t.role === "user" ? "flex-end" : "flex-start",
              maxWidth: "85%",
              background: t.role === "user" ? "var(--cb-red)" : "var(--bg-2)",
              color: t.role === "user" ? "white" : "var(--text-primary)",
              borderRadius: 10, padding: "8px 11px", fontSize: 12.5, whiteSpace: "pre-wrap",
            }}
          >
            {t.content}
          </div>
        ))}
        {sending && <div style={{ fontSize: 12, color: "var(--text-muted)" }}>Thinking...</div>}
      </div>
      <div style={{ padding: 10, borderTop: "1px solid var(--border-subtle)", display: "flex", gap: 8 }}>
        <input
          value={message}
          onChange={(e) => setMessage(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && send()}
          placeholder="Ask a question..."
          style={{ flex: 1 }}
        />
        <button onClick={send} className="cb-btn cb-btn-primary" style={{ padding: "8px 10px" }} disabled={sending}>
          <Send size={14} />
        </button>
      </div>
    </div>
  );
}
