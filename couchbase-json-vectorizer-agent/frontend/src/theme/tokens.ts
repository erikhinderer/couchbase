/** JS-side mirror of theme.css custom properties, for use inside SVG/canvas
 * elements where CSS variables aren't always convenient. */
export const colors = {
  cbRed: "#EA2328",
  cbRedDim: "#7a1215",
  cbRedBright: "#FF4B4F",
  cbTeal: "#00A7B5",
  cbAmber: "#F2A900",
  cbGreen: "#2ECC71",
  cbBlue: "#4C9AFF",
  cbPurple: "#9B6BFF",
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
    case "success":
    case "ready":
    case "healthy":
      return colors.cbGreen;
    case "warning":
      return colors.cbAmber;
    case "error":
    case "failed":
      return colors.cbRedBright;
    case "progress":
    case "backfilling":
    case "validating":
      return colors.cbTeal;
    case "watching":
      return colors.cbPurple;
    default:
      return colors.cbBlue;
  }
};
