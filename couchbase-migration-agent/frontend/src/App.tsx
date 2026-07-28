import { NavLink, Route, Routes } from "react-router-dom";
import { LayoutDashboard, PlusCircle, MessageSquareText } from "lucide-react";
import { CouchbaseWordmark } from "@/assets/CouchbaseLogo";
import DashboardPage from "@/pages/DashboardPage";
import NewMigrationPage from "@/pages/NewMigrationPage";
import MigrationDetailPage from "@/pages/MigrationDetailPage";
import AgentPanel from "@/components/agent/AgentPanel";
import { useWizardStore } from "@/store/wizardStore";

export default function App() {
  const wizardReset = useWizardStore((s) => s.reset);
  return (
    <div style={{ display: "flex", height: "100vh", background: "var(--bg-0)" }}>
      <aside
        style={{
          width: 240,
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
        <NavItem to="/" icon={<LayoutDashboard size={16} />} label="Migrations" end />
        <NavItem
          to="/new"
          icon={<PlusCircle size={16} />}
          label="New Migration"
          onClick={wizardReset}
        />
        <div style={{ marginTop: "auto", padding: "8px" }}>
          <div style={{ fontSize: 11, color: "var(--text-muted)" }}>
            Supports Couchbase Server 7.2.0 – 8.0.2
          </div>
        </div>
      </aside>

      <main style={{ flex: 1, overflow: "auto" }} className="cb-scrollbar">
        <Routes>
          <Route path="/" element={<DashboardPage />} />
          <Route path="/new" element={<NewMigrationPage />} />
          <Route path="/migrations/:id" element={<MigrationDetailPage />} />
        </Routes>
      </main>

      <AgentPanel />
    </div>
  );
}

function NavItem({
  to,
  icon,
  label,
  end,
  onClick,
}: {
  to: string;
  icon: React.ReactNode;
  label: string;
  end?: boolean;
  onClick?: () => void;
}) {
  return (
    <NavLink
      to={to}
      end={end}
      onClick={onClick}
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
