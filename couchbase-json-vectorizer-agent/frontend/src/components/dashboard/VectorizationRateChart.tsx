import { useEffect, useState } from "react";
import { AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid } from "recharts";

interface Point { t: string; docsPerMinute: number }

/** Rolling window chart of documents vectorized per minute, fed by the live
 * websocket stats stream. */
export default function VectorizationRateChart({ docsPerMinute }: { docsPerMinute: number }) {
  const [series, setSeries] = useState<Point[]>([]);

  useEffect(() => {
    setSeries((s) => {
      const next = [...s, { t: new Date().toLocaleTimeString(), docsPerMinute }];
      return next.slice(-40);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [docsPerMinute]);

  return (
    <div className="cb-card" style={{ padding: 16, height: 220 }}>
      <div style={{ fontSize: 12, fontWeight: 700, color: "var(--text-secondary)", marginBottom: 8 }}>
        DOCUMENTS VECTORIZED / MIN
      </div>
      <ResponsiveContainer width="100%" height="85%">
        <AreaChart data={series}>
          <defs>
            <linearGradient id="docsPerMinute" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#00A7B5" stopOpacity={0.5} />
              <stop offset="100%" stopColor="#00A7B5" stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="3 3" stroke="#2A3140" />
          <XAxis dataKey="t" tick={{ fontSize: 9, fill: "#6B7484" }} minTickGap={30} />
          <YAxis tick={{ fontSize: 9, fill: "#6B7484" }} width={30} />
          <Tooltip contentStyle={{ background: "#191E2A", border: "1px solid #2A3140", fontSize: 12 }} />
          <Area type="monotone" dataKey="docsPerMinute" stroke="#00A7B5" fill="url(#docsPerMinute)" strokeWidth={2} />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
