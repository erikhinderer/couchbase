import { useEffect, useRef, useState } from "react";
import { MessageSquareText, Send, X, Sparkles } from "lucide-react";
import { chatWithAgent } from "@/api/client";
import { useParams } from "react-router-dom";
import { useWizardStore } from "@/store/wizardStore";
import { useMigrationSocket } from "@/hooks/useMigrationSocket";

interface ChatMsg {
  role: "user" | "assistant";
  content: string;
}

// Only auto-surface bottleneck findings while a migration is actually in-flight --
// otherwise simply opening an old, already-finished migration's detail page would
// replay its entire history of past findings as if they'd just happened.
const ACTIVE_PHASES = new Set([
  "backup_in_progress", "migrating", "replicating", "verifying", "validating",
]);

export default function AgentPanel() {
  const [open, setOpen] = useState(false);
  const [messages, setMessages] = useState<ChatMsg[]>([
    {
      role: "assistant",
      content:
        "Hi, I'm the Migration Agent assistant. Ask me about validation failures, " +
        "migration strategy, or what happened on a past migration — I remember prior runs. " +
        "I also watch active backups and restores for common bottlenecks (thread/CPU " +
        "contention, memory pressure, stalled or degraded throughput) based on Couchbase's " +
        "own troubleshooting guidance. For a backup that's overloading the source cluster's " +
        "CPU or memory, I'll automatically restart it with fewer threads and tell you here " +
        "when I do — for anything I can't safely fix myself (restore-side bottlenecks, or a " +
        "stalled/degraded transfer that isn't a thread-count problem), I'll pop up here with " +
        "what I found and a concrete suggestion instead.",
    },
  ]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const params = useParams();
  const wizard = useWizardStore();
  // The route param covers the migration detail page; the wizard store's own
  // migrationId covers the New Migration wizard's Backup step, where the user is
  // watching a live backup but hasn't navigated to /migrations/:id yet.
  const migrationId = params.id || wizard.migrationId || undefined;

  // Proactive bottleneck notifications: listen on the same per-migration websocket
  // channel the dashboard/wizard already use for live progress, and surface any new
  // BottleneckFinding the backend appends to MigrationRecord.bottleneck_findings as
  // an assistant message -- auto-opening the panel, since these can fire while the
  // user hasn't opened it at all.
  const { data: live } = useMigrationSocket(migrationId || "none");
  const seenFindingIds = useRef<Set<string>>(new Set());

  useEffect(() => {
    const record = live as any;
    if (!record || !ACTIVE_PHASES.has(record.phase)) return;
    const findings: any[] = record.bottleneck_findings || [];
    if (!findings.length) return;
    const fresh = findings.filter((f) => !seenFindingIds.current.has(f.finding_id));
    if (!fresh.length) return;
    fresh.forEach((f) => seenFindingIds.current.add(f.finding_id));
    setMessages((m) => [
      ...m,
      ...fresh.map((f) => ({
        role: "assistant" as const,
        // auto_remediated findings are only ever posted after the agent has already
        // acted (stopped and relaunched the backup at a lower thread count) -- lead
        // with that instead of "detected", and show what was done rather than a
        // suggestion to act on. Everything else (including the detection finding
        // that preceded a remediation) is diagnosis + a suggestion for the user.
        content: f.auto_remediated
          ? `🔧 Bottleneck resolved automatically during ${f.phase} (${f.cluster_label}):\n${f.message}\n\n${f.suggestion}`
          : `⚠ Bottleneck detected during ${f.phase} (${f.cluster_label}):\n${f.message}\n\nSuggestion: ${f.suggestion}`,
      })),
    ]);
    setOpen(true);
  }, [live]);

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
