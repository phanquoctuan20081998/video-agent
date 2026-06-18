import React from "react";
import {
  AbsoluteFill,
  CalculateMetadataFunction,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { BRAND } from "./BrandConfig";

export interface FactCounterProps {
  fact_number: string;
  headline: string;
  detail?: string;
  tag?: string;
  duration_s?: number;
  accent_color?: string;
}

export const calculateMetadata: CalculateMetadataFunction<FactCounterProps> = async ({ props }) => ({
  durationInFrames: Math.round((props.duration_s ?? 5) * 30),
});

export const FactCounter: React.FC<FactCounterProps> = ({
  fact_number,
  headline,
  detail,
  tag = "SỰ THẬT",
  accent_color = "#FFD400",
}) => {
  const frame = useCurrentFrame();
  const { fps, durationInFrames } = useVideoConfig();
  const enter = spring({ fps, frame, config: { damping: 16, stiffness: 120 } });
  const numberEnter = spring({ fps, frame: frame - 8, config: { damping: 14, stiffness: 160 } });
  const exit = interpolate(frame, [durationInFrames - 12, durationInFrames], [1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  return (
    <AbsoluteFill style={{ background: "#080808", justifyContent: "center", padding: 100, overflow: "hidden" }}>
      <div
        style={{
          position: "absolute",
          inset: 0,
          background:
            "linear-gradient(90deg, rgba(255,212,0,0.16), transparent 45%), radial-gradient(circle at 80% 20%, rgba(255,255,255,0.08), transparent 22%)",
        }}
      />
      <div style={{ display: "flex", alignItems: "center", gap: 56, opacity: exit }}>
        <div
          style={{
            width: 330,
            height: 330,
            border: `16px solid ${accent_color}`,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            background: "#c0001d",
            boxShadow: "0 28px 70px rgba(0,0,0,0.55)",
            transform: `scale(${interpolate(numberEnter, [0, 1], [0.62, 1])}) rotate(${interpolate(numberEnter, [0, 1], [-8, 0])}deg)`,
          }}
        >
          <div
            style={{
              fontFamily: BRAND.font,
              fontSize: 142,
              fontWeight: 950,
              color: "white",
              textShadow: "0 7px 0 #000",
            }}
          >
            {fact_number}
          </div>
        </div>

        <div style={{ maxWidth: 1160, transform: `translateX(${interpolate(enter, [0, 1], [80, 0])}px)` }}>
          <div
            style={{
              display: "inline-block",
              fontFamily: BRAND.font,
              fontSize: 36,
              fontWeight: 950,
              color: "#080808",
              background: accent_color,
              padding: "10px 18px",
              marginBottom: 26,
            }}
          >
            {tag}
          </div>
          <div
            style={{
              fontFamily: BRAND.font,
              fontSize: 94,
              fontWeight: 950,
              color: "white",
              lineHeight: 1.03,
              textShadow: "0 6px 0 #000",
            }}
          >
            {headline}
          </div>
          {detail && (
            <div
              style={{
                marginTop: 26,
                fontFamily: BRAND.font,
                fontSize: 48,
                fontWeight: 760,
                color: "#f2f2f2",
                lineHeight: 1.18,
                maxWidth: 1000,
              }}
            >
              {detail}
            </div>
          )}
        </div>
      </div>
    </AbsoluteFill>
  );
};
