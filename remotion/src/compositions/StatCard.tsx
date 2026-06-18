import React from "react";
import {
  AbsoluteFill,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
  CalculateMetadataFunction,
} from "remotion";
import { BRAND } from "./BrandConfig";

export interface StatCardProps {
  value: string;
  label: string;
  context?: string;
  duration_s?: number;
  accent_color?: string;
  prefix?: string;
  suffix?: string;
}

export const calculateMetadata: CalculateMetadataFunction<StatCardProps> = async ({ props }) => ({
  durationInFrames: Math.round((props.duration_s ?? 5) * 30),
});

export const StatCard: React.FC<StatCardProps> = ({
  value,
  label,
  context,
  accent_color = BRAND.accent,
  prefix = "",
  suffix = "",
}) => {
  const frame = useCurrentFrame();
  const { fps, durationInFrames } = useVideoConfig();

  const numericValue = parseFloat(value.replace(/[^0-9.]/g, "")) || 0;
  const isNumeric = !isNaN(numericValue) && numericValue > 0;

  const countProgress = spring({ fps, frame, config: { damping: 20, stiffness: 80, mass: 1.5 } });
  const labelProgress = spring({ fps, frame: frame - 10, config: { damping: 16, stiffness: 150 } });
  const contextProgress = spring({ fps, frame: frame - 20, config: { damping: 16, stiffness: 120 } });

  const displayValue = isNumeric
    ? Math.round(interpolate(countProgress, [0, 1], [0, numericValue])).toLocaleString()
    : value;

  const exitStart = durationInFrames - 12;
  const exit = interpolate(frame, [exitStart, durationInFrames], [1, 0], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });

  const scaleIn = interpolate(countProgress, [0, 1], [0.5, 1]);

  return (
    <AbsoluteFill style={{ backgroundColor: BRAND.bg, justifyContent: "center", alignItems: "center", padding: BRAND.padding }}>
      <div style={{ textAlign: "center", opacity: exit }}>
        <div style={{
          fontFamily: BRAND.font,
          fontSize: 200,
          fontWeight: 900,
          color: accent_color,
          lineHeight: 0.9,
          letterSpacing: "-0.04em",
          transform: `scale(${scaleIn})`,
          opacity: interpolate(countProgress, [0, 0.2, 1], [0, 1, 1]),
        }}>
          {prefix}{displayValue}{suffix}
        </div>

        <div style={{
          fontFamily: BRAND.font,
          fontSize: BRAND.bodySize,
          fontWeight: 700,
          color: BRAND.text,
          marginTop: 32,
          textTransform: "uppercase",
          letterSpacing: "0.1em",
          transform: `translateY(${interpolate(labelProgress, [0, 1], [30, 0])}px)`,
          opacity: labelProgress,
        }}>
          {label}
        </div>

        {context && (
          <div style={{
            fontFamily: BRAND.font,
            fontSize: BRAND.captionSize,
            fontWeight: 400,
            color: BRAND.textMuted,
            marginTop: 16,
            maxWidth: 900,
            opacity: contextProgress,
          }}>
            {context}
          </div>
        )}
      </div>
    </AbsoluteFill>
  );
};
