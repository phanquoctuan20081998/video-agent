import React from "react";
import {
  AbsoluteFill,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
  CalculateMetadataFunction,
  Audio,
  staticFile,
} from "remotion";
import { BRAND } from "./BrandConfig";

export interface CaptionWord {
  word: string;
  start_s: number;
  end_s: number;
}

export interface CaptionBarProps {
  words: CaptionWord[];
  duration_s?: number;
  bg_overlay?: boolean;
  font_size?: number;
  accent_color?: string;
  position?: "top" | "bottom";
}

export const calculateMetadata: CalculateMetadataFunction<CaptionBarProps> = async ({ props }) => ({
  durationInFrames: Math.round((props.duration_s ?? 10) * 30),
});

export const CaptionBar: React.FC<CaptionBarProps> = ({
  words,
  bg_overlay = false,
  font_size = BRAND.captionSize * 1.2,
  accent_color = BRAND.accent,
  position = "bottom",
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const currentTime = frame / fps;

  // Group words into lines of ~7 words, tracking which are "active"
  const LINE_SIZE = 7;
  const lines: CaptionWord[][] = [];
  for (let i = 0; i < words.length; i += LINE_SIZE) {
    lines.push(words.slice(i, i + LINE_SIZE));
  }

  // Find active line
  const activeWordIdx = words.findIndex(
    (w) => currentTime >= w.start_s && currentTime < w.end_s
  );
  const activeLine = activeWordIdx >= 0 ? Math.floor(activeWordIdx / LINE_SIZE) : -1;

  if (activeLine < 0) return <AbsoluteFill />;

  const lineWords = lines[activeLine];
  const lineProgress = spring({ fps, frame: frame - Math.round(lineWords[0].start_s * fps), config: { damping: 20, stiffness: 200 } });

  return (
    <AbsoluteFill style={{ justifyContent: position === "bottom" ? "flex-end" : "flex-start", alignItems: "center" }}>
      <div style={{
        background: bg_overlay ? "rgba(0,0,0,0.75)" : "transparent",
        padding: "20px 60px",
        borderRadius: BRAND.radius,
        marginBottom: position === "bottom" ? 80 : 0,
        marginTop: position === "top" ? 80 : 0,
        transform: `translateY(${interpolate(lineProgress, [0, 1], [20, 0])}px)`,
        opacity: lineProgress,
        display: "flex",
        gap: "0.35em",
        flexWrap: "wrap",
        justifyContent: "center",
        maxWidth: 1600,
      }}>
        {lineWords.map((w, i) => {
          const isActive = currentTime >= w.start_s && currentTime < w.end_s;
          const wasActive = currentTime >= w.end_s;
          return (
            <span
              key={i}
              style={{
                fontFamily: BRAND.font,
                fontSize: font_size,
                fontWeight: 800,
                color: isActive ? accent_color : wasActive ? BRAND.text : BRAND.textMuted,
                transition: "color 0.1s",
                textShadow: "0 2px 8px rgba(0,0,0,0.8)",
                letterSpacing: "-0.01em",
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
