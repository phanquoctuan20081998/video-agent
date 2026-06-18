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

export interface QuoteCardProps {
  quote: string;
  attribution?: string;
  year?: string;
  duration_s?: number;
  accent_color?: string;
}

export const calculateMetadata: CalculateMetadataFunction<QuoteCardProps> = async ({ props }) => ({
  durationInFrames: Math.round((props.duration_s ?? 6) * 30),
});

export const QuoteCard: React.FC<QuoteCardProps> = ({
  quote,
  attribution,
  year,
  accent_color = BRAND.accent,
}) => {
  const frame = useCurrentFrame();
  const { fps, durationInFrames } = useVideoConfig();

  const quoteMarkProgress = spring({ fps, frame, config: { damping: 12, stiffness: 140 } });
  const textProgress = spring({ fps, frame: frame - 10, config: { damping: 16, stiffness: 130 } });
  const attrProgress = spring({ fps, frame: frame - 20, config: { damping: 16, stiffness: 120 } });

  const exitStart = durationInFrames - 12;
  const exit = interpolate(frame, [exitStart, durationInFrames], [1, 0], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });

  return (
    <AbsoluteFill style={{ backgroundColor: BRAND.bg, justifyContent: "center", alignItems: "center", padding: BRAND.padding }}>
      <div style={{ width: "100%", opacity: exit }}>
        <div style={{
          fontFamily: BRAND.font,
          fontSize: 180,
          fontWeight: 900,
          color: accent_color,
          lineHeight: 0.6,
          marginBottom: 12,
          transform: `scale(${interpolate(quoteMarkProgress, [0, 1], [2, 1])}) translateX(${interpolate(quoteMarkProgress, [0, 1], [-40, 0])}px)`,
          opacity: quoteMarkProgress,
        }}>
          "
        </div>

        <div style={{
          fontFamily: BRAND.font,
          fontSize: BRAND.bodySize * 1.1,
          fontWeight: 500,
          color: BRAND.text,
          lineHeight: 1.5,
          fontStyle: "italic",
          maxWidth: 1500,
          transform: `translateY(${interpolate(textProgress, [0, 1], [40, 0])}px)`,
          opacity: textProgress,
        }}>
          {quote}
        </div>

        {attribution && (
          <div style={{
            marginTop: 32,
            display: "flex",
            alignItems: "center",
            gap: 16,
            opacity: attrProgress,
            transform: `translateY(${interpolate(attrProgress, [0, 1], [20, 0])}px)`,
          }}>
            <div style={{ height: 3, width: 48, backgroundColor: accent_color, borderRadius: 2 }} />
            <div style={{
              fontFamily: BRAND.font,
              fontSize: BRAND.captionSize,
              fontWeight: 700,
              color: BRAND.textMuted,
              letterSpacing: "0.05em",
            }}>
              {attribution}{year ? `, ${year}` : ""}
            </div>
          </div>
        )}
      </div>
    </AbsoluteFill>
  );
};
