import React from "react";
import {
  AbsoluteFill,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
  CalculateMetadataFunction,
} from "remotion";
import { BRAND, easeOut } from "./BrandConfig";

export interface KineticTextProps {
  text: string;
  duration_s?: number;
  accent_words?: string[];
  bg_color?: string;
  font_size?: number;
  align?: "left" | "center" | "right";
}

export const calculateMetadata: CalculateMetadataFunction<KineticTextProps> = async ({ props }) => ({
  durationInFrames: Math.round((props.duration_s ?? 5) * 30),
});

export const KineticText: React.FC<KineticTextProps> = ({
  text,
  accent_words = [],
  bg_color = BRAND.bg,
  font_size = BRAND.titleSize,
  align = "center",
}) => {
  const frame = useCurrentFrame();
  const { fps, durationInFrames } = useVideoConfig();
  const words = text.split(" ");
  const staggerFrames = Math.max(3, Math.floor((durationInFrames * 0.6) / words.length));

  const exitStart = durationInFrames - 15;
  const globalExit = interpolate(frame, [exitStart, durationInFrames], [1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: easeOut,
  });

  return (
    <AbsoluteFill
      style={{
        backgroundColor: bg_color,
        justifyContent: "center",
        alignItems: "center",
        padding: BRAND.padding,
      }}
    >
      <div
        style={{
          display: "flex",
          flexWrap: "wrap",
          gap: "0.25em",
          justifyContent: align === "center" ? "center" : align === "left" ? "flex-start" : "flex-end",
          opacity: globalExit,
        }}
      >
        {words.map((word, i) => {
          const delay = i * staggerFrames;
          const progress = spring({
            fps,
            frame: frame - delay,
            config: { damping: 14, stiffness: 180, mass: 0.8 },
          });
          const isAccent =
            accent_words.includes(word) ||
            accent_words.includes(word.replace(/[^a-zA-Z0-9]/g, ""));

          return (
            <span
              key={i}
              style={{
                fontFamily: BRAND.font,
                fontSize: font_size,
                fontWeight: 900,
                color: isAccent ? BRAND.accent : BRAND.text,
                transform: `translateY(${interpolate(progress, [0, 1], [80, 0])}px) scale(${interpolate(progress, [0, 1], [0.7, 1])})`,
                opacity: interpolate(progress, [0, 0.3, 1], [0, 1, 1]),
                display: "inline-block",
                lineHeight: 1.1,
                letterSpacing: "-0.02em",
              }}
            >
              {word}
            </span>
          );
        })}
      </div>
    </AbsoluteFill>
  );
};
