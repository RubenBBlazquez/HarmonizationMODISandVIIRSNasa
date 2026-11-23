// Datos de la API → GeoJSON listo para las capas del mapa (sin tocar el mapa).
import type { FireCollection, Properties } from "../../api/types.js";
import { NO_CATEGORY_COLOR, yearColor } from "../../domain/colors.js";
import { pixelHalfSizeDegrees, sortByConfidence } from "../../domain/detections.js";
import { landCoverKeys } from "../../domain/landCover.js";

export const EMPTY: GeoJSON.FeatureCollection = { type: "FeatureCollection", features: [] };

/** Recuadro en grados. */
export interface Bounds {
  west: number;
  south: number;
  east: number;
  north: number;
}

/** Polígono de un recuadro. */
export function boxPolygon({ west, south, east, north }: Bounds): GeoJSON.Polygon {
  return {
    type: "Polygon",
    coordinates: [[[west, south], [east, south], [east, north], [west, north], [west, south]]],
  };
}

/**
 * Propiedades auxiliares para filtrar y pintar sin recalcular: orden de pintado, año, clases de
 * suelo, día, hora y color del año.
 */
export function withHelpers(properties: Properties, dateField: string, order: number): Properties {
  const date = String(properties[dateField] ?? "");
  const year = Number(date.slice(0, 4));
  return {
    ...properties,
    _order: order,
    _year: date.slice(0, 4),
    _lcs: `|${landCoverKeys(properties.land_cover).join("|")}|`, // "|crops|trees|"
    _day: date.slice(0, 10) || "9999-12-31",
    _t: date || "9999-12-31T23:59:59Z", // hora exacta: avance pasada a pasada de un incendio
    _yc: year ? yearColor(year) : NO_CATEGORY_COLOR,
  };
}

/** Puntos ordenados para pintarlos y sus píxeles (polígonos ancho × alto). */
export function detectionLayers(collection: FireCollection | null): {
  points: GeoJSON.FeatureCollection;
  pixels: GeoJSON.FeatureCollection;
} {
  if (!collection) return { points: EMPTY, pixels: EMPTY };
  const points: GeoJSON.Feature[] = [];
  const pixels: GeoJSON.Feature[] = [];
  sortByConfidence(collection).features.forEach((feature, order) => {
    if (feature.geometry.type !== "Point") return;
    const [lng, lat] = feature.geometry.coordinates;
    const properties = withHelpers(feature.properties, "acq_datetime", order);
    points.push({ type: "Feature", geometry: feature.geometry, properties });
    const [halfWidth, halfHeight] = pixelHalfSizeDegrees(lat, feature.properties);
    pixels.push({
      type: "Feature",
      properties,
      geometry: boxPolygon({
        west: lng - halfWidth,
        east: lng + halfWidth,
        south: lat - halfHeight,
        north: lat + halfHeight,
      }),
    });
  });
  return {
    points: { type: "FeatureCollection", features: points },
    pixels: { type: "FeatureCollection", features: pixels },
  };
}

export function eventsLayer(collection: FireCollection | null): GeoJSON.FeatureCollection {
  if (!collection) return EMPTY;
  return {
    type: "FeatureCollection",
    features: collection.features.map((feature, order) => ({
      ...feature,
      properties: withHelpers(feature.properties, "first_detection", order),
    })),
  };
}

/** Centro de un píxel (polígono ancho × alto): media de sus esquinas. */
export function pixelCenter(geometry: GeoJSON.Geometry): GeoJSON.Point | null {
  if (geometry.type === "Point") return geometry;
  if (geometry.type !== "Polygon") return null;
  const corners = geometry.coordinates[0].slice(0, -1);
  if (!corners.length) return null;
  const sum = corners.reduce(([x, y], [cx, cy]) => [x + cx, y + cy], [0, 0]);
  return { type: "Point", coordinates: [sum[0] / corners.length, sum[1] / corners.length] };
}

/** Todas las posiciones [lng, lat] de una geometría. */
export function positionsOf(geometry: GeoJSON.Geometry): [number, number][] {
  const positions: [number, number][] = [];
  const visit = (value: unknown): void => {
    if (Array.isArray(value) && typeof value[0] === "number") {
      positions.push([value[0] as number, value[1] as number]);
    } else if (Array.isArray(value)) value.forEach(visit);
  };
  if ("coordinates" in geometry) visit(geometry.coordinates);
  return positions;
}
