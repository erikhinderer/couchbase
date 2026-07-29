/** JS-side mirror of theme.css custom properties, for use where CSS variables
 * aren't convenient (e.g. computed stroke colors inside an inline SVG diagram). */
export const colors = {
  cbRed: "#EA2328",
  cbRedDim: "#7A1215",
  cbRedBright: "#FF4B4F",
  cbTeal: "#00A7B5",
  cbAmber: "#F2A900",
  cbGreen: "#2ECC71",
  cbBlue: "#4C9AFF",
  bg0: "#0B0E14",
  bg1: "#12161F",
  bg2: "#191E2A",
  bg3: "#232936",
  borderSubtle: "#2A3140",
  borderStrong: "#3A4256",
  textPrimary: "#E8EAED",
  textSecondary: "#9AA4B2",
  textMuted: "#6B7484",
} as const;

export const statusColor = (status: string): string => {
  switch (status) {
    case "complete":
    case "validated":
    case "healthy":
      return colors.cbGreen;
    case "awaiting_approval":
    case "validation_failed":
      return colors.cbAmber;
    case "failed":
    case "rolled_back":
      return colors.cbRedBright;
    case "migrating":
    case "replicating":
    case "validating":
    case "verifying":
      return colors.cbTeal;
    default:
      return colors.cbBlue;
  }
};

export const SOURCE_TYPE_LABELS: Record<string, string> = {
  mongodb: "MongoDB",
  dynamodb: "Amazon DynamoDB",
  redis: "Redis",
  cassandra: "Apache Cassandra",
  cosmosdb: "Microsoft Azure Cosmos DB",
};
