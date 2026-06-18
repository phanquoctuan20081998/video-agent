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

export interface SplitComparisonProps {
  left_label: string;
  left_items: string[];
  right_label: string;
  right_items: string[];
  title?: string;
  duration_s?: number;
  accent_color?: string;
}

export const calculateMetadata: CalculateMetadataFunction<SplitComparisonProps> = async ({ props }) => ({
  durationInFrames: Math.round((props.duration_s ?? 8) * 30),
});

export const SplitComparison: React.FC<SplitComparisonProps> = ({
  left_label,
  left_items,
  right_label,
  right_items,
  title,
  accent_color = BRAND.accent,
}) => {
  const frame = useCurrentFrame();
  const { fps, durationInFrames } = useVideoConfig();

  const titleProgress = spring({ fps, frame, config: { damping: 16, stiffness: 150 } });
  const dividerProgress = spring({ fps, frame: frame - 8, config: { damping: 20, stiffness: 120 } });
  const leftProgress = spring({ fps, frame: frame - 12, config: { damping: 16, stiffness: 140 } });
  const rightProgress = spring({ fps, frame: frame - 16, config: { damping: 16, stiffness: 140 } });

  const exitStart = durationInFrames - 12;
  const exit = interpolate(frame, [exitStart, durationInFrames], [1, 0], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });

  const Column: React.FC<{ label: string; items: string[]; side: "left" | "right"; progress: number }> = ({
    label, items, side, progress,
  }) => (
    <div style={{
      flex: 1,
      transform: `translateX(${interpolate(progress, [0, 1], [side === "left" ? -60 : 60, 0])}px)`,
      opacity: progress,
    }}>
      <div style={{
        fontFamily: BRAND.font,
        fontSize: BRAND.captionSize,
        fontWeight: 900,
        color: side === "left" ? BRAND.text : accent_color,
        textTransform: "uppercase",
        letterSpacing: "0.1em",
        marginBottom: 28,
        paddingBottom: 16,
        borderBottom: `3px solid ${side === "left" ? BRAND.bgLight : accent_color}`,
      }}>
        {label}
      </div>
      {items.map((item, i) => (
        <div key={i} style={{
          fontFamily: BRAND.font,
          fontSize: BRAND.bodySize * 0.85,
          fontWeight: 400,
          color: BRAND.text,
          lineHeight: 1.4,
          marginBottom: 18,
          paddingLeft: 16,
          borderLeft: `3px solid ${side === "left" ? BRAND.bgLight : accent_color}44`,
        }}>
          {item}
        </div>
      ))}
    </div>
  );

  return (
    <AbsoluteFill style={{ backgroundColor: BRAND.bg, padding: BRAND.padding, justifyContent: "center", opacity: exit }}>
      {title && (
        <div style={{
          fontFamily: BRAND.font,
          fontSize: BRAND.bodySize,
          fontWeight: 700,
          color: BRAND.textMuted,
          marginBottom: 48,
          textTransform: "uppercase",
          letterSpacing: "0.1em",
          opacity: titleProgress,
        }}>
          {title}
        </div>
      )}
      <div style={{ display: "flex", gap: 0, alignItems: "flex-start" }}>
        <Column label={left_label} items={left_items} side="left" progress={leftProgress} />
        <div style={{
          width: 2,
          alignSelf: "stretch",
          backgroundColor: BRAND.bgLight,
          margin: "0 60px",
          transform: `scaleY(${dividerProgress})`,
          transformOrigin: "top",
        }} />
        <Column label={right_label} items={right_items} side="right" progress={rightProgress} />
      </div>
    </AbsoluteFill>
  );
};
