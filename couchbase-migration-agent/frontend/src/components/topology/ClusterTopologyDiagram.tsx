import { colors, statusColor } from "@/theme/tokens";
import { CouchbaseMark } from "@/assets/CouchbaseLogo";

export interface TopologyNodeInfo {
  hostname: string;
  services: string[];
  status?: string;
}

export interface TopologySide {
  label: string;
  isCapella?: boolean;
  version?: string | null;
  nodes: TopologyNodeInfo[];
  buckets: string[];
  xdcrRemotes?: { name: string; hostname: string }[];
}

interface Props {
  source: TopologySide | null;
  destination: TopologySide | null;
  phase: string;
  throughputMbPerSec?: number;
}

/**
 * AWS-DMS-console-style topology: source cluster card -> migration agent (flow) ->
 * destination Capella card, with XDCR remotes rendered as dashed satellite nodes off
 * the source when a cross-datacenter topology is detected.
 */
export default function ClusterTopologyDiagram({ source, destination, phase, throughputMbPerSec = 0 }: Props) {
  const flowing = ["migrating", "backup_in_progress", "verifying", "replicating"].includes(phase);
  const isContinuous = phase === "replicating";
  const flowColor = statusColor(flowing ? "progress" : phase === "complete" ? "success" : "info");

  return (
    <div className="cb-card" style={{ padding: 24, overflow: "hidden" }}>
      <svg viewBox="0 0 900 320" width="100%" height="320" role="img" aria-label="Cluster topology diagram">
        <defs>
          <marker id="arrow" markerWidth="10" markerHeight="10" refX="8" refY="5" orient="auto">
            <path d="M0,0 L10,5 L0,10 Z" fill={flowColor} />
          </marker>
        </defs>

        {/* Source cluster card */}
        <ClusterCard x={20} y={70} width={260} height={180} side={source} accent={colors.cbTeal} title="Source Cluster" />

        {/* XDCR remotes, if any, drawn above the source */}
        {source?.xdcrRemotes?.slice(0, 2).map((r, i) => (
          <g key={r.name}>
            <rect
              x={20 + i * 140} y={4} width={130} height={50} rx={8}
              fill={colors.bg2} stroke={colors.borderStrong} strokeDasharray="4 3"
            />
            <text x={20 + i * 140 + 10} y={24} fill={colors.textSecondary} fontSize={10} fontWeight={700}>
              XDCR REMOTE
            </text>
            <text x={20 + i * 140 + 10} y={40} fill={colors.textPrimary} fontSize={11}>
              {r.name}
            </text>
            <line
              x1={20 + i * 140 + 65} y1={54} x2={150} y2={70}
              stroke={colors.cbAmber} strokeDasharray="3 3" strokeWidth={1.5}
            />
          </g>
        ))}

        {/* Flow line: source -> agent -> destination */}
        <line x1={280} y1={160} x2={400} y2={160} stroke={flowColor} strokeWidth={2.5} markerEnd="url(#arrow)" />
        <line x1={500} y1={160} x2={620} y2={160} stroke={flowColor} strokeWidth={2.5} markerEnd="url(#arrow)" />

        {flowing && (
          <>
            <circle r="4" fill={flowColor}>
              <animateMotion dur={isContinuous ? "1s" : "1.6s"} repeatCount="indefinite" path="M280,160 L400,160" />
            </circle>
            <circle r="4" fill={flowColor}>
              <animateMotion dur={isContinuous ? "1s" : "1.6s"} begin="0.5s" repeatCount="indefinite" path="M500,160 L620,160" />
            </circle>
            {isContinuous && (
              <>
                <circle r="4" fill={flowColor} opacity={0.6}>
                  <animateMotion dur="1s" begin="0.33s" repeatCount="indefinite" path="M280,160 L400,160" />
                </circle>
                <circle r="4" fill={flowColor} opacity={0.6}>
                  <animateMotion dur="1s" begin="0.83s" repeatCount="indefinite" path="M500,160 L620,160" />
                </circle>
              </>
            )}
          </>
        )}

        {isContinuous && (
          <g transform="translate(450,190)">
            <circle r="4" fill={colors.cbAmber}>
              <animate attributeName="opacity" values="1;0.2;1" dur="1.5s" repeatCount="indefinite" />
            </circle>
            <text x={10} y={4} fontSize={10} fontWeight={700} fill={colors.cbAmber}>
              CONTINUOUS SYNC
            </text>
          </g>
        )}

        {/* Migration agent node (center) */}
        <g transform="translate(400,110)">
          <rect width={100} height={100} rx={16} fill={colors.bg2} stroke={colors.cbRed} strokeWidth={1.5} />
          <foreignObject x={18} y={16} width={64} height={40}>
            <CouchbaseMark size={36} />
          </foreignObject>
          <text x={50} y={78} textAnchor="middle" fill={colors.textSecondary} fontSize={10} fontWeight={700}>
            MIGRATION
          </text>
          <text x={50} y={92} textAnchor="middle" fill={colors.textSecondary} fontSize={10} fontWeight={700}>
            AGENT
          </text>
        </g>

        {throughputMbPerSec > 0 && (
          <text x={450} y={100} textAnchor="middle" fill={colors.cbTeal} fontSize={11} fontWeight={700}>
            {throughputMbPerSec.toFixed(1)} MB/s
          </text>
        )}

        {/* Destination cluster card */}
        <ClusterCard x={620} y={70} width={260} height={180} side={destination} accent={colors.cbRed} title="Destination (Capella)" />
      </svg>
    </div>
  );
}

function ClusterCard({
  x, y, width, height, side, accent, title,
}: {
  x: number; y: number; width: number; height: number;
  side: TopologySide | null; accent: string; title: string;
}) {
  const nodeCount = side?.nodes.length ?? 0;
  const bucketCount = side?.buckets.length ?? 0;
  return (
    <g transform={`translate(${x},${y})`}>
      <rect width={width} height={height} rx={14} fill={colors.bg2} stroke={colors.borderStrong} strokeWidth={1.5} />
      <rect width={width} height={36} rx={14} fill={accent} opacity={0.15} />
      <text x={14} y={23} fontSize={12} fontWeight={700} fill={colors.textPrimary}>
        {title}
      </text>
      <text x={14} y={54} fontSize={11} fill={colors.textSecondary}>
        {side?.label ?? "Not configured"}
      </text>
      <text x={14} y={70} fontSize={10} fill={colors.textMuted}>
        {side?.version ? `Couchbase Server ${side.version}` : side?.isCapella ? "Couchbase Capella" : ""}
      </text>

      {/* Node grid */}
      {Array.from({ length: Math.min(nodeCount, 6) }).map((_, i) => (
        <g key={i} transform={`translate(${14 + (i % 3) * 78}, ${86 + Math.floor(i / 3) * 46})`}>
          <rect width={68} height={36} rx={6} fill={colors.bg3} stroke={colors.borderSubtle} />
          <circle cx={12} cy={12} r={4} fill={colors.cbGreen} />
          <text x={22} y={16} fontSize={9} fill={colors.textSecondary}>
            node {i + 1}
          </text>
          <text x={10} y={29} fontSize={8} fill={colors.textMuted}>
            {(side?.nodes[i]?.services || []).join(",") || "kv"}
          </text>
        </g>
      ))}

      <text x={14} y={height - 12} fontSize={10} fill={colors.textMuted}>
        {bucketCount} bucket{bucketCount === 1 ? "" : "s"} · {nodeCount} node{nodeCount === 1 ? "" : "s"}
      </text>
    </g>
  );
}
