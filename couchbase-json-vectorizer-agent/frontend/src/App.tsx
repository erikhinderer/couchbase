import { useEffect, useState } from "react";
import { NavLink, Route, Routes } from "react-router-dom";
import { LayoutDashboard, PlusCircle } from "lucide-react";
import { CouchbaseWordmark } from "@/assets/CouchbaseLogo";
import DashboardPage from "@/pages/DashboardPage";
import SetupWizardPage from "@/pages/SetupWizardPage";
import AgentPanel from "@/components/agent/AgentPanel";
import OperationsFeed, { OperationEntry } from "@/components/dashboard/OperationsFeed";
import { useVectorizerSocket } from "@/hooks/useVectorizerSocket";

export default function App() {
  const { data: liveUpdate } = useVectorizerSocket("*");
  const [ops, setOps] = useState<OperationEntry[]>([]);

  useEffect(() => {
    if (!liveUpdate) return;
    const incoming: OperationEntry[] = (liveUpdate as any).recent_ops || [];
    if (!incoming.length) return;
    setOps((prev) => {
      const merged = new Map(prev.map((o) => [o.id, o]));
      for (const op of incoming) merged.set(op.id, op);
      return Array.from(merged.values())
        .sort((a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime())
        .slice(0, 200);
    });
  }, [liveUpdate]);

  return (
    <div style={{ display: "flex", height: "100vh", background: "var(--bg-0)" }}>
      <aside
        style={{
          width: 232,
          flexShrink: 0,
          borderRight: "1px solid var(--border-subtle)",
          background: "var(--bg-1)",
          display: "flex",
          flexDirection: "column",
          padding: "18px 14px",
          gap: 4,
        }}
      >
        <div style={{ padding: "4px 8px 20px" }}>
          <CouchbaseWordmark />
        </div>
        <NavItem to="/" icon={<LayoutDashboard size={16} />} label="Dashboard" end />
        <NavItem to="/setup" icon={<PlusCircle size={16} />} label="New Agent" />
        <div style={{ marginTop: "auto", padding: "8px" }}>
          <div style={{ fontSize: 11, color: "var(--text-muted)" }}>
            Requires Couchbase Enterprise / Capella 7.6.0+
          </div>
        </div>
      </aside>

      <main style={{ flex: 1, overflow: "auto" }} className="cb-scrollbar">
        <Routes>
          <Route path="/" element={<DashboardPage />} />
          <Route path="/setup" element={<SetupWizardPage />} />
        </Routes>
      </main>

      <OperationsFeed ops={ops} />
      <AgentPanel />
    </div>
  );
}

function NavItem({
  to,
  icon,
  label,
  end,
}: {
  to: string;
  icon: React.ReactNode;
  label: string;
  end?: boolean;
}) {
  return (
    <NavLink
      to={to}
      end={end}
      style={({ isActive }) => ({
        display: "flex",
        alignItems: "center",
        gap: 10,
        padding: "9px 12px",
        borderRadius: "var(--radius-sm)",
        fontSize: 13,
        fontWeight: 600,
        color: isActive ? "var(--text-primary)" : "var(--text-secondary)",
        background: isActive ? "var(--bg-3)" : "transparent",
        borderLeft: isActive ? "3px solid var(--cb-red)" : "3px solid transparent",
      })}
    >
      {icon}
      {label}
    </NavLink>
  );
}
