import { useState } from "react";
import { MessageSquareText, Send, X, Sparkles } from "lucide-react";
import { chatWithAgent } from "@/api/client";

interface ChatMsg {
  role: "user" | "assistant";
  content: string;
}

/** Floating assistant, powered by the local Qwen 3.8 LLM. Rendered as an
 * overlay drawer (rather than a persistent aside like the Migration Agent's
 * AgentPanel) because the right rail in this app is already occupied by the
 * live Operations Feed. */
export default function AgentPanel() {
  const [open, setOpen] = useState(false);
  const [messages, setMessages] = useState<ChatMsg[]>([
    {
      role: "assistant",
      content:
        "Hi, I'm the Vectorizer Agent assistant. Ask me about validation failures, " +
        "which embedding model to pick, or what the dashboard metrics mean.",
    },
  ]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);

  async function send() {
    if (!input.trim()) return;
    const userMsg: ChatMsg = { role: "user", content: input.trim() };
    setMessages((m) => [...m, userMsg]);
    setInput("");
    setLoading(true);
    try {
      const res: any = await chatWithAgent(userMsg.content);
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
        style={{ position: "fixed", right: 344, bottom: 24, borderRadius: 999, padding: "12px 18px", zIndex: 20 }}
      >
        <Sparkles size={16} /> Ask the agent
      </button>
    );
  }

  return (
    <div
      style={{
        position: "fixed",
        right: 344,
        bottom: 24,
        width: 360,
        height: 460,
        zIndex: 20,
        borderRadius: "var(--radius-lg)",
        border: "1px solid var(--border-subtle)",
        background: "var(--bg-1)",
        boxShadow: "var(--shadow-card)",
        display: "flex",
        flexDirection: "column",
        overflow: "hidden",
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
          <MessageSquareText size={16} color="var(--cb-teal)" /> Vectorizer Agent
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
          placeholder="Ask about this agent…"
        />
        <button onClick={send} className="cb-btn cb-btn-primary" disabled={loading}>
          <Send size={14} />
        </button>
      </div>
    </div>
  );
}
