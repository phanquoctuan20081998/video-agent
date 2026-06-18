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

export interface ListRevealProps {
  title?: string;
  items: string[];
  duration_s?: number;
  accent_color?: string;
  style?: "bullet" | "numbered" | "check";
}

export const calculateMetadata: CalculateMetadataFunction<ListRevealProps> = async ({ props }) => ({
  durationInFrames: Math.round((props.duration_s ?? 8) * 30),
});

export const ListReveal: React.FC<ListRevealProps> = ({
  title,
  items,
  accent_color = BRAND.accent,
  style = "bullet",
}) => {
  const frame = useCurrentFrame();
  const { fps, durationInFrames } = useVideoConfig();

  const titleProgress = spring({ fps, frame, config: { damping: 16, stiffness: 150 } });
  const staggerPerItem = Math.max(8, Math.floor((durationInFrames * 0.7) / items.length));

  const exitStart = durationInFrames - 12;
  const exit = interpolate(frame, [exitStart, durationInFrames], [1, 0], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });

  const getMarker = (i: number) => {
    if (style === "numbered") return `${i + 1}.`;
    if (style === "check") return "✓";
    return "·";
  };

  return (
    <AbsoluteFill style={{ backgroundColor: BRAND.bg, padding: BRAND.padding, justifyContent: "center", opacity: exit }}>
      <div>
        {title && (
          <div style={{
            fontFamily: BRAND.font,
            fontSize: BRAND.bodySize,
            fontWeight: 700,
            color: BRAND.textMuted,
            textTransform: "uppercase",
            letterSpacing: "0.12em",
            marginBottom: 48,
            opacity: titleProgress,
          }}>
            {title}
          </div>
        )}

        {items.map((item, i) => {
          const delay = (title ? 12 : 0) + i * staggerPerItem;
          const itemProgress = spring({ fps, frame: frame - delay, config: { damping: 16, stiffness: 160 } });

          return (
            <div
              key={i}
              style={{
                display: "flex",
                alignItems: "flex-start",
                gap: 28,
                marginBottom: 28,
                transform: `translateX(${interpolate(itemProgress, [0, 1], [-60, 0])}px)`,
                opacity: itemProgress,
              }}
            >
              <span style={{
                fontFamily: BRAND.fontMono,
                fontSize: BRAND.bodySize,
                fontWeight: 900,
                color: accent_color,
                flexShrink: 0,
                lineHeight: 1.2,
                minWidth: 48,
              }}>
                {getMarker(i)}
              </span>
              <span style={{
                fontFamily: BRAND.font,
                fontSize: BRAND.bodySize,
                fontWeight: 500,
                color: BRAND.text,
                lineHeight: 1.3,
              }}>
                {item}
              </span>
            </div>
          );
        })}
      </div>
    </AbsoluteFill>
  );
};
