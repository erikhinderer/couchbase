import { useEffect, useRef, useState } from "react";
import { Area, AreaChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { colors } from "@/theme/tokens";

interface Point {
  t: number;
  value: number;
}

/** Rolling client-side history of a single numeric stat, sampled every time the
 * websocket delivers a new MigrationRecord -- the backend itself doesn't persist a
 * time series, only the latest snapshot, so the chart's window is whatever this
 * component has observed since it mounted. */
export default function ThroughputChart({ value, label }: { value: number; label: string }) {
  const [history, setHistory] = useState<Point[]>([]);
  const lastRef = useRef<number>(0);

  useEffect(() => {
    const now = Date.now();
    if (now - lastRef.current < 900) return;
    lastRef.current = now;
    setHistory((prev) => [...prev.slice(-59), { t: now, value }]);
  }, [value]);

  return (
    <div className="cb-card" style={{ padding: 16 }}>
      <div style={{ fontSize: 11, color: "var(--text-muted)", fontWeight: 700, textTransform: "uppercase", marginBottom: 8 }}>
        {label}
      </div>
      <ResponsiveContainer width="100%" height={140}>
        <AreaChart data={history}>
          <defs>
            <linearGradient id="throughputFill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={colors.cbTeal} stopOpacity={0.5} />
              <stop offset="100%" stopColor={colors.cbTeal} stopOpacity={0} />
            </linearGradient>
          </defs>
          <XAxis dataKey="t" hide />
          <YAxis hide domain={[0, "auto"]} />
          <Tooltip
            contentStyle={{ background: colors.bg2, border: `1px solid ${colors.borderSubtle}`, borderRadius: 8, fontSize: 12 }}
            labelFormatter={() => ""}
            formatter={(v: number) => [v.toFixed(1), label]}
          />
          <Area type="monotone" dataKey="value" stroke={colors.cbTeal} strokeWidth={2} fill="url(#throughputFill)" />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
