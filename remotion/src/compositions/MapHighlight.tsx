import React from "react";
import {
  AbsoluteFill,
  CalculateMetadataFunction,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { allCountriesFC, resolveRegion } from "./geo/worldData";
import { buildDrawOnPath, fitFeature, fitFeatureCover, fitWorld, lerpCamera, pathFor } from "./geo/geoMath";

export interface MapHighlightProps extends Record<string, unknown> {
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

const FRAME = { width: 1920, height: 1080 };

const clamp = (value: number, min: number, max: number) => Math.min(max, Math.max(min, value));

const RealMap: React.FC<{
  region: string;
  accent_color: string;
  frame: number;
  fps: number;
}> = ({
  region,
  accent_color,
  frame,
  fps,
}) => {
  const match = resolveRegion(region)!;
  const { width, height } = FRAME;

  const enter = spring({ fps, frame, config: { damping: 22, stiffness: 50 } });
  const wideCamera = fitWorld(allCountriesFC, width, height);
  const containedContinentCamera = fitFeature(match.outline, width, height, 150);
  const closeCamera =
    match.kind === "continent"
      ? { ...containedContinentCamera, translateY: containedContinentCamera.translateY + 70 }
      : fitFeatureCover(match.outline, width, height, 170, 1.18);
  const camera = lerpCamera(wideCamera, closeCamera, Math.min(Math.max(enter, 0), 1));
  const path = pathFor(camera);

  const drawProgress = interpolate(frame, [10, 55], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const drawOn = buildDrawOnPath(match.outline.geometry, camera);
  const label = region.trim() || match.displayName;
  const [rawLabelX, rawLabelY] = path.centroid(match.outline);
  const labelX = clamp(Number.isFinite(rawLabelX) ? rawLabelX : width / 2, 180, width - 180);
  const labelY = clamp(Number.isFinite(rawLabelY) ? rawLabelY : height / 2, 120, height - 120);
  const labelProgress = interpolate(frame, [38, 58], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} style={{ display: "block" }}>
      <rect x={0} y={0} width={width} height={height} fill="#0a1814" />
      {allCountriesFC.features.map((f, i) => (
        <path key={i} d={path(f) ?? undefined} fill="#13261f" stroke="#1f3b2f" strokeWidth={1.5} />
      ))}
      <path d={path(match.outline) ?? undefined} fill={accent_color} fillOpacity={0.28} />
      {drawOn.d && (
        <path
          d={drawOn.d}
          fill="none"
          stroke={accent_color}
          strokeWidth={6}
          strokeDasharray={drawOn.length}
          strokeDashoffset={drawOn.length * (1 - drawProgress)}
          style={{ filter: `drop-shadow(0 0 10px ${accent_color})` }}
        />
      )}
      <g opacity={labelProgress} transform={`translate(0 ${interpolate(labelProgress, [0, 1], [16, 0])})`}>
        <text
          x={labelX}
          y={labelY}
          textAnchor="middle"
          dominantBaseline="middle"
          fill="#f8fff7"
          stroke="#06110f"
          strokeWidth={12}
          paintOrder="stroke"
          fontFamily="Inter, Arial, sans-serif"
          fontSize={match.kind === "continent" ? 58 : 46}
          fontWeight={900}
          letterSpacing={0}
          style={{ filter: "drop-shadow(0 4px 12px rgba(0,0,0,0.75))" }}
        >
          {label}
        </text>
        <text
          x={labelX}
          y={labelY}
          textAnchor="middle"
          dominantBaseline="middle"
          fill={accent_color}
          fontFamily="Inter, Arial, sans-serif"
          fontSize={match.kind === "continent" ? 58 : 46}
          fontWeight={900}
          letterSpacing={0}
        >
          {label}
        </text>
      </g>
    </svg>
  );
};

export const MapHighlight: React.FC<MapHighlightProps> = ({ region, accent_color = "#FFD400" }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const match = resolveRegion(region);

  return (
    <AbsoluteFill style={{ overflow: "hidden" }}>
      {match ? (
        <RealMap region={region} accent_color={accent_color} frame={frame} fps={fps} />
      ) : (
        <AbsoluteFill
          style={{
            backgroundColor: "#0a1814",
            justifyContent: "center",
            alignItems: "center",
          }}
        >
          <div
            style={{
              width: 500,
              height: 500,
              background: "linear-gradient(145deg, #ff3535 0%, #b90020 70%)",
              clipPath:
                "polygon(48% 3%, 62% 17%, 57% 32%, 71% 37%, 79% 56%, 65% 63%, 57% 91%, 43% 92%, 34% 67%, 18% 58%, 25% 40%, 16% 25%, 34% 18%)",
            }}
          />
        </AbsoluteFill>
      )}
    </AbsoluteFill>
  );
};
