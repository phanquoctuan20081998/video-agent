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

export interface KineticTypographyWord {
  word: string;
  start_s: number;
  end_s: number;
  emphasis?: boolean;
}

export interface KineticTypographyProps {
  words: KineticTypographyWord[];
  duration_s?: number;
  accent_color?: string;
  bg_color?: string;
}

export const calculateMetadata: CalculateMetadataFunction<KineticTypographyProps> = async ({ props }) => ({
  durationInFrames: Math.round((props.duration_s ?? 10) * 30),
});

export const KineticTypography: React.FC<KineticTypographyProps> = ({
  words,
  accent_color = BRAND.accent,
  bg_color = BRAND.bg,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const currentTime = frame / fps;

  const WINDOW = 4;
  const visibleStart = Math.max(0, Math.floor(words.findIndex((w) => w.start_s > currentTime - 3)));
  const visibleWords = words.slice(visibleStart, visibleStart + WINDOW);

  return (
    <AbsoluteFill
      style={{
        backgroundColor: bg_color,
        justifyContent: "center",
        alignItems: "center",
        padding: BRAND.padding,
      }}
    >
      <div style={{
        display: "flex",
        flexWrap: "wrap",
        justifyContent: "center",
        gap: "0.3em",
        maxWidth: 1600,
      }}>
        {visibleWords.map((w, i) => {
          const isActive = currentTime >= w.start_s && currentTime < w.end_s;
          const progress = spring({
            fps,
            frame: Math.round((currentTime - w.start_s) * fps),
            config: { damping: 14, stiffness: 200 },
          });

          return (
            <span
              key={`${visibleStart + i}`}
              style={{
                fontFamily: BRAND.font,
                fontSize: w.emphasis ? BRAND.titleSize * 1.3 : BRAND.titleSize,
                fontWeight: 900,
                color: isActive ? accent_color : BRAND.text,
                opacity: isActive ? 1 : 0.3,
                transform: isActive
                  ? `scale(${interpolate(progress, [0, 1], [0.85, 1])}) translateY(${interpolate(progress, [0, 1], [30, 0])}px)`
                  : "scale(1)",
                display: "inline-block",
                letterSpacing: "-0.03em",
                lineHeight: 1.1,
                transition: "opacity 0.15s, color 0.15s",
              }}
            >
              {w.word}
            </span>
          );
        })}
      </div>
    </AbsoluteFill>
  );
};
