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

export interface DefinitionCardProps {
  term: string;
  definition: string;
  etymology?: string;
  duration_s?: number;
  accent_color?: string;
}

export const calculateMetadata: CalculateMetadataFunction<DefinitionCardProps> = async ({ props }) => ({
  durationInFrames: Math.round((props.duration_s ?? 6) * 30),
});

export const DefinitionCard: React.FC<DefinitionCardProps> = ({
  term,
  definition,
  etymology,
  accent_color = BRAND.accent,
}) => {
  const frame = useCurrentFrame();
  const { fps, durationInFrames } = useVideoConfig();

  const termProgress = spring({ fps, frame, config: { damping: 14, stiffness: 160 } });
  const lineProgress = spring({ fps, frame: frame - 8, config: { damping: 20, stiffness: 220 } });
  const defProgress = spring({ fps, frame: frame - 15, config: { damping: 16, stiffness: 130 } });
  const etmProgress = spring({ fps, frame: frame - 25, config: { damping: 16, stiffness: 120 } });

  const exitStart = durationInFrames - 12;
  const exit = interpolate(frame, [exitStart, durationInFrames], [1, 0], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });

  return (
    <AbsoluteFill style={{ backgroundColor: BRAND.bg, justifyContent: "center", padding: BRAND.padding }}>
      <div style={{ opacity: exit }}>
        <div style={{
          fontFamily: BRAND.fontMono,
          fontSize: BRAND.smallSize,
          color: accent_color,
          textTransform: "uppercase",
          letterSpacing: "0.15em",
          marginBottom: 12,
          opacity: termProgress,
        }}>
          noun / phrase
        </div>

        <div style={{
          fontFamily: BRAND.font,
          fontSize: BRAND.titleSize * 1.1,
          fontWeight: 900,
          color: BRAND.text,
          lineHeight: 1,
          letterSpacing: "-0.03em",
          transform: `translateY(${interpolate(termProgress, [0, 1], [60, 0])}px)`,
          opacity: termProgress,
        }}>
          {term}
        </div>

        <div style={{
          height: 4,
          width: `${interpolate(lineProgress, [0, 1], [0, 100])}%`,
          backgroundColor: accent_color,
          marginTop: 20,
          marginBottom: 32,
          borderRadius: 2,
        }} />

        <div style={{
          fontFamily: BRAND.font,
          fontSize: BRAND.bodySize,
          fontWeight: 400,
          color: BRAND.text,
          lineHeight: 1.5,
          maxWidth: 1400,
          transform: `translateY(${interpolate(defProgress, [0, 1], [30, 0])}px)`,
          opacity: defProgress,
        }}>
          {definition}
        </div>

        {etymology && (
          <div style={{
            fontFamily: BRAND.fontMono,
            fontSize: BRAND.captionSize,
            color: BRAND.textMuted,
            marginTop: 24,
            opacity: etmProgress,
          }}>
            ← {etymology}
          </div>
        )}
      </div>
    </AbsoluteFill>
  );
};
