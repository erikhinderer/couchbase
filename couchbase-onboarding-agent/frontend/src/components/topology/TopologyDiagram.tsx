import { colors } from "@/theme/tokens";
import { SOURCE_TYPE_LABELS } from "@/theme/tokens";
import type { MigrationPhase, SourceType } from "@/api/types";

const ACTIVE_PHASES: MigrationPhase[] = ["migrating", "replicating", "verifying"];

export default function TopologyDiagram({
  sourceType,
  sourceLabel,
  destLabel,
  bucket,
  phase,
}: {
  sourceType: SourceType;
  sourceLabel: string;
  destLabel: string;
  bucket: string;
  phase: MigrationPhase;
}) {
  const active = ACTIVE_PHASES.includes(phase);
  const strokeColor = active ? colors.cbTeal : colors.borderStrong;

  return (
    <svg viewBox="0 0 640 160" width="100%" height="160">
      <line x1="150" y1="80" x2="320" y2="80" stroke={strokeColor} strokeWidth={2} strokeDasharray={active ? "6 4" : undefined} />
      <line x1="320" y1="80" x2="490" y2="80" stroke={strokeColor} strokeWidth={2} strokeDasharray={active ? "6 4" : undefined} />

      <g>
        <rect x="10" y="45" width="140" height="70" rx="10" fill={colors.bg2} stroke={colors.borderSubtle} />
        <text x="80" y="75" textAnchor="middle" fill={colors.textPrimary} fontSize="12" fontWeight={700}>
          {SOURCE_TYPE_LABELS[sourceType] ?? sourceType}
        </text>
        <text x="80" y="93" textAnchor="middle" fill={colors.textMuted} fontSize="10.5">
          {sourceLabel}
        </text>
      </g>

      <g>
        <rect x="255" y="35" width="130" height="90" rx="10" fill={colors.bg2} stroke={active ? colors.cbTeal : colors.borderSubtle} />
        <text x="320" y="70" textAnchor="middle" fill={colors.textPrimary} fontSize="12" fontWeight={700}>
          Onboarding
        </text>
        <text x="320" y="86" textAnchor="middle" fill={colors.textPrimary} fontSize="12" fontWeight={700}>
          Agent
        </text>
        <text x="320" y="104" textAnchor="middle" fill={colors.textMuted} fontSize="10">
          extract &middot; transform &middot; load
        </text>
      </g>

      <g>
        <rect x="490" y="45" width="140" height="70" rx="10" fill={colors.bg2} stroke={colors.cbRed} />
        <text x="560" y="72" textAnchor="middle" fill={colors.textPrimary} fontSize="12" fontWeight={700}>
          Couchbase
        </text>
        <text x="560" y="90" textAnchor="middle" fill={colors.textMuted} fontSize="10.5">
          {destLabel} &middot; {bucket}
        </text>
      </g>
    </svg>
  );
}
