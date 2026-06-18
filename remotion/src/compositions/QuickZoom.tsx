import React from "react";
import {
  AbsoluteFill,
  Img,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
  CalculateMetadataFunction,
  staticFile,
} from "remotion";
import { BRAND } from "./BrandConfig";

export interface QuickZoomProps {
  image_url: string;
  caption?: string;
  zoom_start?: number;
  zoom_end?: number;
  pan_x?: number;
  pan_y?: number;
  duration_s?: number;
  accent_color?: string;
}

export const calculateMetadata: CalculateMetadataFunction<QuickZoomProps> = async ({ props }) => ({
  durationInFrames: Math.round((props.duration_s ?? 6) * 30),
});

export const QuickZoom: React.FC<QuickZoomProps> = ({
  image_url,
  caption,
  zoom_start = 1.05,
  zoom_end = 1.25,
  pan_x = 0,
  pan_y = 0,
  accent_color = BRAND.accent,
}) => {
  const frame = useCurrentFrame();
  const { fps, durationInFrames } = useVideoConfig();

  const progress = interpolate(frame, [0, durationInFrames], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  const zoom = interpolate(progress, [0, 1], [zoom_start, zoom_end]);
  const captionProgress = spring({ fps, frame: frame - 8, config: { damping: 16, stiffness: 150 } });

  return (
    <AbsoluteFill style={{ backgroundColor: BRAND.bg, overflow: "hidden" }}>
      <div style={{
        position: "absolute", inset: 0,
        transform: `scale(${zoom}) translate(${pan_x * progress}%, ${pan_y * progress}%)`,
        transformOrigin: "center",
      }}>
        <Img
          src={image_url}
          style={{ width: "100%", height: "100%", objectFit: "cover" }}
        />
      </div>

      {caption && (
        <AbsoluteFill style={{ justifyContent: "flex-end", alignItems: "flex-start", padding: "0 80px 80px" }}>
          <div style={{
            background: `linear-gradient(135deg, ${accent_color}EE, ${accent_color}99)`,
            padding: "16px 28px",
            borderRadius: BRAND.radius,
            fontFamily: BRAND.font,
            fontSize: BRAND.captionSize,
            fontWeight: 700,
            color: BRAND.bg,
            opacity: captionProgress,
            transform: `translateY(${interpolate(captionProgress, [0, 1], [20, 0])}px)`,
          }}>
            {caption}
          </div>
        </AbsoluteFill>
      )}
    </AbsoluteFill>
  );
};
