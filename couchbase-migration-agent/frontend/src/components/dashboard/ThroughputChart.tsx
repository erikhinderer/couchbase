import { useEffect, useState } from "react";
import { AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid } from "recharts";

interface Point { t: string; mbps: number; docsPerSec: number }

interface Props {
  mbps: number;
  docsPerSec: number;
  label?: string;
}

/** Rolling window chart of throughput, fed by the live websocket stats stream.
 * `label` lets callers repurpose the same chart for a different unit (e.g.
 * mutations/sec while a continuous replication is running) without implying
 * it's still measuring MB/s. */
export default function ThroughputChart({ mbps, docsPerSec, label = "THROUGHPUT (MB/s)" }: Props) {
  const [series, setSeries] = useState<Point[]>([]);

  useEffect(() => {
    setSeries((s) => {
      const next = [...s, { t: new Date().toLocaleTimeString(), mbps, docsPerSec }];
      return next.slice(-40);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mbps, docsPerSec]);

  return (
    <div className="cb-card" style={{ padding: 16, height: 220 }}>
      <div style={{ fontSize: 12, fontWeight: 700, color: "var(--text-secondary)", marginBottom: 8 }}>
        {label}
      </div>
      <ResponsiveContainer width="100%" height="85%">
        <AreaChart data={series}>
          <defs>
            <linearGradient id="mbps" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#00A7B5" stopOpacity={0.5} />
              <stop offset="100%" stopColor="#00A7B5" stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="3 3" stroke="#2A3140" />
          <XAxis dataKey="t" tick={{ fontSize: 9, fill: "#6B7484" }} minTickGap={30} />
          <YAxis tick={{ fontSize: 9, fill: "#6B7484" }} width={30} />
          <Tooltip contentStyle={{ background: "#191E2A", border: "1px solid #2A3140", fontSize: 12 }} />
          <Area type="monotone" dataKey="mbps" stroke="#00A7B5" fill="url(#mbps)" strokeWidth={2} />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
