import { useState } from "react";
import { MessageSquareText, Send, X, Sparkles } from "lucide-react";
import { chatWithAgent } from "@/api/client";
import { useParams } from "react-router-dom";

interface ChatMsg {
  role: "user" | "assistant";
  content: string;
}

export default function AgentPanel() {
  const [open, setOpen] = useState(false);
  const [messages, setMessages] = useState<ChatMsg[]>([
    {
      role: "assistant",
      content:
        "Hi, I'm the Migration Agent assistant. Ask me about validation failures, " +
        "migration strategy, or what happened on a past migration — I remember prior runs.",
    },
  ]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const params = useParams();
  const migrationId = params.id;

  async function send() {
    if (!input.trim()) return;
    const userMsg: ChatMsg = { role: "user", content: input.trim() };
    setMessages((m) => [...m, userMsg]);
    setInput("");
    setLoading(true);
    try {
      const res: any = await chatWithAgent(userMsg.content, migrationId);
      setMessages((m) => [...m, { role: "assistant", content: res.reply }]);
    } catch (e: any) {
      setMessages((m) => [...m, { role: "assistant", content: `(agent unavailable: ${e.message})` }]);
    } finally {
      setLoading(false);
    }
  }

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="cb-btn cb-btn-primary"
        style={{ position: "fixed", right: 24, bottom: 24, borderRadius: 999, padding: "12px 18px" }}
      >
        <Sparkles size={16} /> Ask the agent
      </button>
    );
  }

  return (
    <aside
      style={{
        width: 340,
        borderLeft: "1px solid var(--border-subtle)",
        background: "var(--bg-1)",
        display: "flex",
        flexDirection: "column",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "14px 16px",
          borderBottom: "1px solid var(--border-subtle)",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 8, fontWeight: 700, fontSize: 13 }}>
          <MessageSquareText size={16} color="var(--cb-teal)" /> Migration Agent
        </div>
        <button onClick={() => setOpen(false)} className="cb-btn" style={{ padding: 4 }}>
          <X size={14} />
        </button>
      </div>

      <div style={{ flex: 1, overflow: "auto", padding: 16, display: "flex", flexDirection: "column", gap: 12 }} className="cb-scrollbar">
        {messages.map((m, i) => (
          <div
            key={i}
            style={{
              alignSelf: m.role === "user" ? "flex-end" : "flex-start",
              maxWidth: "88%",
              background: m.role === "user" ? "var(--cb-red-dim)" : "var(--bg-2)",
              border: "1px solid var(--border-subtle)",
              borderRadius: "var(--radius-md)",
              padding: "8px 12px",
              fontSize: 13,
              whiteSpace: "pre-wrap",
            }}
          >
            {m.content}
          </div>
        ))}
        {loading && <div style={{ fontSize: 12, color: "var(--text-muted)" }}>Thinking…</div>}
      </div>

      <div style={{ display: "flex", gap: 8, padding: 12, borderTop: "1px solid var(--border-subtle)" }}>
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && send()}
          placeholder="Ask about this migration…"
          style={{
            flex: 1,
            background: "var(--bg-2)",
            border: "1px solid var(--border-strong)",
            borderRadius: "var(--radius-sm)",
            color: "var(--text-primary)",
            padding: "8px 10px",
            fontSize: 13,
          }}
        />
        <button onClick={send} className="cb-btn cb-btn-primary" disabled={loading}>
          <Send size={14} />
        </button>
      </div>
    </aside>
  );
}
