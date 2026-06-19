import { geoMercator, geoPath } from "d3-geo";
import type { Feature, FeatureCollection, MultiPolygon, Polygon, Position } from "geojson";

export interface Camera {
  scale: number;
  translateX: number;
  translateY: number;
}

function fit(
  subject: Feature | FeatureCollection,
  width: number,
  height: number,
  padding: number
): Camera {
  const projection = geoMercator().fitExtent(
    [[padding, padding], [width - padding, height - padding]],
    subject
  );
  const [translateX, translateY] = projection.translate();
  return { scale: projection.scale(), translateX, translateY };
}

export function fitWorld(allCountries: FeatureCollection, width: number, height: number): Camera {
  return fit(allCountries, width, height, 40);
}

export function fitFeature(
  subject: Feature<Polygon | MultiPolygon>,
  width: number,
  height: number,
  padding: number
): Camera {
  return fit(subject, width, height, padding);
}

/** Like fitFeature, but COVERS the frame (may crop the longer axis) instead of
 * containing it with letterboxing — for a dramatic full-bleed zoom on a 16:9 frame
 * where the subject's aspect ratio doesn't match (e.g. a tall continent).
 * Capped at maxZoomRatio beyond the "contain" scale so extreme aspect-ratio subjects
 * (a tall narrow country like Vietnam) don't get cropped into unrecognizable strips. */
export function fitFeatureCover(
  subject: Feature<Polygon | MultiPolygon>,
  width: number,
  height: number,
  padding: number,
  maxZoomRatio: number = 1.35
): Camera {
  const refScale = 1000;
  const probe = geoPath(geoMercator().scale(refScale).translate([0, 0]).center([0, 0]));
  const bounds = probe.bounds(subject);
  const bboxW = bounds[1][0] - bounds[0][0];
  const bboxH = bounds[1][1] - bounds[0][1];

  const scaleX = ((width - 2 * padding) / bboxW) * refScale;
  const scaleY = ((height - 2 * padding) / bboxH) * refScale;
  const containScale = Math.min(scaleX, scaleY);
  const coverScale = Math.max(scaleX, scaleY);
  const scale = Math.min(coverScale, containScale * maxZoomRatio);

  const centered = geoPath(geoMercator().scale(scale).translate([0, 0]).center([0, 0]));
  const b2 = centered.bounds(subject);
  const cx = (b2[0][0] + b2[1][0]) / 2;
  const cy = (b2[0][1] + b2[1][1]) / 2;

  return { scale, translateX: width / 2 - cx, translateY: height / 2 - cy };
}

export function lerpCamera(a: Camera, b: Camera, t: number): Camera {
  return {
    scale: a.scale + (b.scale - a.scale) * t,
    translateX: a.translateX + (b.translateX - a.translateX) * t,
    translateY: a.translateY + (b.translateY - a.translateY) * t,
  };
}

export function projectionFromCamera(camera: Camera) {
  return geoMercator().scale(camera.scale).translate([camera.translateX, camera.translateY]);
}

export function pathFor(camera: Camera) {
  return geoPath(projectionFromCamera(camera));
}

/** Largest ring (by point count) of a Polygon/MultiPolygon — used as the "main landmass" for border draw-on. */
function mainRing(geometry: Polygon | MultiPolygon): Position[] {
  const rings: Position[][] = geometry.type === "Polygon" ? [geometry.coordinates[0]] : geometry.coordinates.map((p) => p[0]);
  return rings.reduce((best, ring) => (ring.length > best.length ? ring : best), rings[0]);
}

export interface DrawOnPath {
  d: string;
  length: number;
}

/** Manually projects the main outer ring and builds an SVG path + its pixel length, for stroke-dashoffset draw-on. */
export function buildDrawOnPath(geometry: Polygon | MultiPolygon, camera: Camera): DrawOnPath {
  const projection = projectionFromCamera(camera);
  const ring = mainRing(geometry);
  const points = ring.map((lonLat) => projection(lonLat as [number, number])).filter((p): p is [number, number] => p !== null);

  if (points.length === 0) return { d: "", length: 0 };

  let d = `M${points[0][0]},${points[0][1]}`;
  let length = 0;
  for (let i = 1; i < points.length; i++) {
    d += `L${points[i][0]},${points[i][1]}`;
    length += Math.hypot(points[i][0] - points[i - 1][0], points[i][1] - points[i - 1][1]);
  }
  length += Math.hypot(points[0][0] - points[points.length - 1][0], points[0][1] - points[points.length - 1][1]);
  d += "Z";

  return { d, length };
}
