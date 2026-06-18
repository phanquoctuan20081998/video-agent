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

export interface TimelineEvent {
  year: string;
  label: string;
  highlight?: boolean;
}

export interface TimelineProps {
  title?: string;
  events: TimelineEvent[];
  duration_s?: number;
  accent_color?: string;
  active_index?: number;
}

export const calculateMetadata: CalculateMetadataFunction<TimelineProps> = async ({ props }) => ({
  durationInFrames: Math.round((props.duration_s ?? 8) * 30),
});

export const Timeline: React.FC<TimelineProps> = ({
  title,
  events,
  accent_color = BRAND.accent,
  active_index,
}) => {
  const frame = useCurrentFrame();
  const { fps, durationInFrames } = useVideoConfig();

  const titleProgress = spring({ fps, frame, config: { damping: 16, stiffness: 150 } });
  const lineProgress = spring({ fps, frame: frame - 8, config: { damping: 20, stiffness: 100, mass: 1.2 } });

  const exitStart = durationInFrames - 12;
  const exit = interpolate(frame, [exitStart, durationInFrames], [1, 0], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });

  return (
    <AbsoluteFill style={{ backgroundColor: BRAND.bg, padding: BRAND.padding, justifyContent: "center", opacity: exit }}>
      {title && (
        <div style={{
          fontFamily: BRAND.font,
          fontSize: BRAND.captionSize,
          fontWeight: 700,
          color: accent_color,
          textTransform: "uppercase",
          letterSpacing: "0.12em",
          marginBottom: 60,
          opacity: titleProgress,
        }}>
          {title}
        </div>
      )}

      <div style={{ position: "relative" }}>
        {/* Spine line */}
        <div style={{
          position: "absolute",
          top: 28,
          left: 0,
          height: 4,
          width: `${interpolate(lineProgress, [0, 1], [0, 100])}%`,
          backgroundColor: BRAND.bgLight,
          borderRadius: 2,
        }} />

        <div style={{ display: "flex", justifyContent: "space-between", gap: 20 }}>
          {events.map((event, i) => {
            const delay = 8 + i * 6;
            const dotProgress = spring({ fps, frame: frame - delay, config: { damping: 14, stiffness: 200 } });
            const textProgress = spring({ fps, frame: frame - delay - 4, config: { damping: 16, stiffness: 150 } });
            const isActive = active_index === i || event.highlight;

            return (
              <div key={i} style={{ flex: 1, display: "flex", flexDirection: "column", alignItems: "flex-start" }}>
                <div style={{
                  width: 28,
                  height: 28,
                  borderRadius: "50%",
                  backgroundColor: isActive ? accent_color : BRAND.text,
                  transform: `scale(${interpolate(dotProgress, [0, 1], [0, 1])})`,
                  marginBottom: 20,
                  zIndex: 1,
                  boxShadow: isActive ? `0 0 20px ${accent_color}88` : "none",
                }} />
                <div style={{
                  fontFamily: BRAND.fontMono,
                  fontSize: BRAND.smallSize,
                  fontWeight: 700,
                  color: isActive ? accent_color : BRAND.textMuted,
                  marginBottom: 8,
                  opacity: textProgress,
                }}>
                  {event.year}
                </div>
                <div style={{
                  fontFamily: BRAND.font,
                  fontSize: BRAND.captionSize,
                  fontWeight: isActive ? 700 : 400,
                  color: isActive ? BRAND.text : BRAND.textMuted,
                  lineHeight: 1.3,
                  opacity: textProgress,
                }}>
                  {event.label}
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </AbsoluteFill>
  );
};
