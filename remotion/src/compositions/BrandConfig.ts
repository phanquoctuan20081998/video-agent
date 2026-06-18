export const BRAND = {
  bg: "#0A0A0A",
  bgLight: "#1A1A1A",
  text: "#FFFFFF",
  textMuted: "#A0A0A0",
  accent: "#FFCC00",
  accentDark: "#CC9900",
  font: "'Inter', 'Helvetica Neue', Arial, sans-serif",
  fontMono: "'IBM Plex Mono', 'Courier New', monospace",
  titleSize: 110,
  bodySize: 64,
  captionSize: 48,
  smallSize: 36,
  radius: 12,
  padding: 100,
};

export const easeOut = (t: number) => 1 - Math.pow(1 - t, 3);
export const easeInOut = (t: number) =>
  t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
