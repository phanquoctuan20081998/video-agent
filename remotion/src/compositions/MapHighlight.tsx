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

export interface MapHighlightProps {
  region: string;
  headline: string;
  subline?: string;
  callouts?: string[];
  marker_label?: string;
  duration_s?: number;
  accent_color?: string;
  bg_color?: string;
}

export const calculateMetadata: CalculateMetadataFunction<MapHighlightProps> = async ({ props }) => ({
  durationInFrames: Math.round((props.duration_s ?? 6) * 30),
});

export const MapHighlight: React.FC<MapHighlightProps> = ({
  region,
  headline,
  subline,
  callouts = [],
  marker_label,
  accent_color = "#FFD400",
  bg_color = "#070707",
}) => {
  const frame = useCurrentFrame();
  const { fps, durationInFrames } = useVideoConfig();
  const enter = spring({ fps, frame, config: { damping: 18, stiffness: 110 } });
  const pulse = Math.sin(frame / 8) * 0.04 + 1;
  const exit = interpolate(frame, [durationInFrames - 14, durationInFrames], [1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  return (
    <AbsoluteFill style={{ backgroundColor: bg_color, overflow: "hidden" }}>
      <AbsoluteFill
        style={{
          background:
            "radial-gradient(circle at 68% 42%, rgba(255,212,0,0.16), transparent 24%), linear-gradient(135deg, #102018 0%, #111 42%, #06110f 100%)",
        }}
      />
      <div
        style={{
          position: "absolute",
          inset: 0,
          opacity: 0.38,
          backgroundImage:
            "linear-gradient(rgba(255,255,255,0.08) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,0.08) 1px, transparent 1px)",
          backgroundSize: "80px 80px",
          transform: `scale(${interpolate(frame, [0, durationInFrames], [1.02, 1.12])})`,
        }}
      />

      <div
        style={{
          position: "absolute",
          right: 170,
          top: 130,
          width: 650,
          height: 650,
          opacity: exit,
          transform: `scale(${interpolate(enter, [0, 1], [0.78, 1]) * pulse}) rotate(-5deg)`,
          filter: "drop-shadow(0 32px 50px rgba(0,0,0,0.65))",
        }}
      >
        <div
          style={{
            width: "100%",
            height: "100%",
            background: "linear-gradient(145deg, #ff3535 0%, #b90020 70%)",
            clipPath:
              "polygon(48% 3%, 62% 17%, 57% 32%, 71% 37%, 79% 56%, 65% 63%, 57% 91%, 43% 92%, 34% 67%, 18% 58%, 25% 40%, 16% 25%, 34% 18%)",
            border: "8px solid white",
          }}
        />
        <div
          style={{
            position: "absolute",
            left: "38%",
            top: "43%",
            fontFamily: BRAND.font,
            fontSize: 42,
            fontWeight: 900,
            color: "white",
            textShadow: "0 3px 0 rgba(0,0,0,0.5)",
          }}
        >
          {marker_label || region}
        </div>
      </div>

      <div
        style={{
          position: "absolute",
          left: 100,
          top: 100,
          width: 900,
          opacity: exit,
          transform: `translateX(${interpolate(enter, [0, 1], [-70, 0])}px)`,
        }}
      >
        <div
          style={{
            fontFamily: BRAND.font,
            fontSize: 54,
            fontWeight: 900,
            color: accent_color,
            textTransform: "uppercase",
            textShadow: "0 4px 0 #000",
            marginBottom: 28,
          }}
        >
          {region}
        </div>
        <div
          style={{
            fontFamily: BRAND.font,
            fontSize: 92,
            fontWeight: 950,
            color: "white",
            lineHeight: 1.04,
            textShadow: "0 6px 0 #000",
          }}
        >
          {headline}
        </div>
        {subline && (
          <div
            style={{
              marginTop: 24,
              fontFamily: BRAND.font,
              fontSize: 58,
              fontWeight: 850,
              color: accent_color,
              lineHeight: 1.12,
              textShadow: "0 4px 0 #000",
            }}
          >
            {subline}
          </div>
        )}
      </div>

      <div style={{ position: "absolute", left: 110, bottom: 90, display: "flex", gap: 18, opacity: exit }}>
        {callouts.slice(0, 3).map((item, index) => (
          <div
            key={item}
            style={{
              fontFamily: BRAND.font,
              fontSize: 34,
              fontWeight: 850,
              color: "white",
              background: "rgba(0,0,0,0.72)",
              borderLeft: `8px solid ${accent_color}`,
              padding: "18px 22px",
              transform: `translateY(${interpolate(spring({ fps, frame: frame - index * 8 }), [0, 1], [26, 0])}px)`,
            }}
          >
            {item}
          </div>
        ))}
      </div>
    </AbsoluteFill>
  );
};
