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

export interface TitleCardProps {
  title: string;
  subtitle?: string;
  duration_s?: number;
  accent_color?: string;
  bg_color?: string;
}

export const calculateMetadata: CalculateMetadataFunction<TitleCardProps> = async ({ props }) => ({
  durationInFrames: Math.round((props.duration_s ?? 5) * 30),
});

export const TitleCard: React.FC<TitleCardProps> = ({
  title,
  subtitle,
  accent_color = BRAND.accent,
  bg_color = BRAND.bg,
}) => {
  const frame = useCurrentFrame();
  const { fps, durationInFrames } = useVideoConfig();

  const titleProgress = spring({ fps, frame, config: { damping: 16, stiffness: 150 } });
  const subtitleProgress = spring({ fps, frame: frame - 12, config: { damping: 16, stiffness: 120 } });
  const lineProgress = spring({ fps, frame: frame - 6, config: { damping: 20, stiffness: 200 } });

  const exitStart = durationInFrames - 12;
  const exit = interpolate(frame, [exitStart, durationInFrames], [1, 0], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });

  return (
    <AbsoluteFill style={{ backgroundColor: bg_color, justifyContent: "center", alignItems: "center", padding: BRAND.padding }}>
      <div style={{ width: "100%", opacity: exit }}>
        <div style={{
          fontFamily: BRAND.font,
          fontSize: BRAND.titleSize,
          fontWeight: 900,
          color: BRAND.text,
          lineHeight: 1.05,
          letterSpacing: "-0.03em",
          transform: `translateX(${interpolate(titleProgress, [0, 1], [-80, 0])}px)`,
          opacity: titleProgress,
        }}>
          {title}
        </div>

        <div style={{
          height: 6,
          width: `${interpolate(lineProgress, [0, 1], [0, 30])}%`,
          backgroundColor: accent_color,
          marginTop: 24,
          marginBottom: 24,
          borderRadius: 3,
        }} />

        {subtitle && (
          <div style={{
            fontFamily: BRAND.font,
            fontSize: BRAND.bodySize,
            fontWeight: 400,
            color: BRAND.textMuted,
            lineHeight: 1.4,
            transform: `translateX(${interpolate(subtitleProgress, [0, 1], [-60, 0])}px)`,
            opacity: subtitleProgress,
          }}>
            {subtitle}
          </div>
        )}
      </div>
    </AbsoluteFill>
  );
};
