// Capa de detecciones dibujada en un único canvas.
//
// Con decenas de miles de detecciones, una capa de Leaflet por detección (cada una con su popup y su
// tooltip) hacía que el zoom fuera a tirones: Leaflet reproyecta y redibuja cada capa en cada paso.
// Aquí las coordenadas se proyectan una sola vez (a zoom 0), el canvas solo se redibuja al terminar
// cada movimiento y durante la animación del zoom se escala con CSS (como los renderers de Leaflet).
// El tooltip y el popup se resuelven buscando la detección bajo el cursor.
import L from "leaflet";

import { trackHoveredTooltip, type FireFeature, type Properties } from "./shared.js";

/** Margen del canvas alrededor de la vista (fracción del tamaño): evita bordes vacíos al arrastrar. */
const PADDING = 0.3;
/** Píxeles de pantalla de tolerancia al pasar el ratón por un punto o un píxel. */
const HIT_TOLERANCE = 1;
/** Lado mínimo (px) con el que se dibuja un píxel para que no desaparezca. */
const MIN_PIXEL_SIDE = 1;

export interface DetectionsCanvasOptions {
  /** Desde este zoom cada detección se dibuja como su píxel; por debajo, como un punto. */
  pixelMinZoom: number;
  /** Radio (px) del punto. */
  pointRadius: number;
  /** Área del píxel (ancho × alto) de la detección. */
  pixelBounds: (latlng: L.LatLng, properties: Properties) => L.LatLngBounds;
  style: (feature: FireFeature, asPixels: boolean) => L.PathOptions;
  popup: (properties: Properties) => string;
  tooltip: (properties: Properties) => string;
}

interface ResolvedStyle {
  color: string;
  weight: number;
  opacity: number;
  fillColor: string;
  fillOpacity: number;
}

// Métodos internos de L.Map que usan los renderers de Leaflet para la animación del zoom.
interface MapInternals {
  _getNewPixelOrigin(center: L.LatLng, zoom: number): L.Point;
}

function resolve(options: L.PathOptions): ResolvedStyle {
  const color = options.color ?? "#3388ff";
  return {
    color,
    weight: options.weight ?? 3,
    opacity: options.opacity ?? 1,
    fillColor: options.fillColor ?? color,
    fillOpacity: options.fillOpacity ?? 0.2,
  };
}

export class DetectionsCanvasLayer extends L.Layer {
  private readonly settings: DetectionsCanvasOptions;
  private features: FireFeature[] = [];
  // Coordenadas proyectadas a zoom 0: centro (x, y) y esquinas del píxel (x0, y0, x1, y1).
  private x = new Float64Array(0);
  private y = new Float64Array(0);
  private rect = new Float64Array(0);
  private styles: { points: ResolvedStyle[] | null; pixels: ResolvedStyle[] | null } = {
    points: null,
    pixels: null,
  };
  private canvas: HTMLCanvasElement | null = null;
  private ctx: CanvasRenderingContext2D | null = null;
  private center: L.LatLng | null = null;
  private zoom = 0;
  private tooltip = L.tooltip({ direction: "top", opacity: 0.95 });
  private hovered = -1;
  private pendingMove: L.LeafletMouseEvent | null = null;
  private frame = 0;

  constructor(settings: DetectionsCanvasOptions) {
    super({ pane: "detectionsPane" } as L.LayerOptions);
    this.settings = settings;
  }

  /** Sustituye las detecciones dibujadas (ya filtradas y en el orden de pintado: la última, encima). */
  setFeatures(features: FireFeature[]): this {
    this.features = features;
    this.styles = { points: null, pixels: null };
    this.project();
    this.hideTooltip();
    if (this._map) this.reset();
    return this;
  }

  override onAdd(map: L.Map): this {
    if (!map.getPane("detectionsPane")) {
      // Debajo de overlayPane (400): los eventos se dibujan encima de las detecciones.
      map.createPane("detectionsPane").style.zIndex = "390";
    }
    const canvas = L.DomUtil.create("canvas", "leaflet-zoom-animated");
    canvas.style.pointerEvents = "none"; // los eventos del ratón se leen del mapa
    this.getPane()!.appendChild(canvas);
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.project();
    this.reset();
    return this;
  }

  override onRemove(): this {
    this.hideTooltip();
    cancelAnimationFrame(this.frame);
    this.canvas?.remove();
    this.canvas = null;
    this.ctx = null;
    return this;
  }

  override getEvents(): { [name: string]: L.LeafletEventHandlerFn } {
    return {
      viewreset: this.reset,
      moveend: this.reset,
      resize: this.reset,
      zoom: this.onZoom,
      zoomanim: this.onZoomAnim as L.LeafletEventHandlerFn,
      zoomstart: this.hideTooltip,
      mousemove: this.onMouseMove as L.LeafletEventHandlerFn,
      mouseout: this.hideTooltip,
      click: this.onClick as L.LeafletEventHandlerFn,
    };
  }

  private project(): void {
    if (!this._map) return;
    const crs = this._map.options.crs ?? L.CRS.EPSG3857;
    const count = this.features.length;
    this.x = new Float64Array(count);
    this.y = new Float64Array(count);
    this.rect = new Float64Array(count * 4);
    this.features.forEach((feature, index) => {
      const latlng = featureLatLng(feature);
      const point = crs.latLngToPoint(latlng, 0);
      this.x[index] = point.x;
      this.y[index] = point.y;
      const bounds = this.settings.pixelBounds(latlng, feature.properties ?? {});
      const northWest = crs.latLngToPoint(bounds.getNorthWest(), 0);
      const southEast = crs.latLngToPoint(bounds.getSouthEast(), 0);
      this.rect.set([northWest.x, northWest.y, southEast.x, southEast.y], index * 4);
    });
  }

  private asPixels(zoom = this._map.getZoom()): boolean {
    return zoom >= this.settings.pixelMinZoom;
  }

  private stylesFor(asPixels: boolean): ResolvedStyle[] {
    const key = asPixels ? "pixels" : "points";
    this.styles[key] ??= this.features.map((feature) => resolve(this.settings.style(feature, asPixels)));
    return this.styles[key]!;
  }

  /** Coloca el canvas sobre la vista (con margen) y vuelve a dibujar. */
  private reset(): void {
    const map = this._map;
    const canvas = this.canvas;
    const ctx = this.ctx;
    if (!map || !canvas || !ctx) return;

    const size = map.getSize();
    const padding = size.multiplyBy(PADDING).round();
    const topLeft = map.containerPointToLayerPoint(padding.multiplyBy(-1));
    const width = size.x + 2 * padding.x;
    const height = size.y + 2 * padding.y;
    const ratio = window.devicePixelRatio || 1;

    L.DomUtil.setPosition(canvas, topLeft);
    canvas.width = Math.round(width * ratio);
    canvas.height = Math.round(height * ratio);
    canvas.style.width = `${width}px`;
    canvas.style.height = `${height}px`;
    this.center = map.getCenter();
    this.zoom = map.getZoom();

    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    ctx.clearRect(0, 0, width, height);

    const scale = 2 ** this.zoom;
    const origin = map.getPixelOrigin();
    const offsetX = origin.x + topLeft.x;
    const offsetY = origin.y + topLeft.y;
    const asPixels = this.asPixels(this.zoom);
    const styles = this.stylesFor(asPixels);
    const radius = this.settings.pointRadius;

    for (let index = 0; index < this.features.length; index += 1) {
      let left: number;
      let top: number;
      let right: number;
      let bottom: number;
      if (asPixels) {
        const base = index * 4;
        left = this.rect[base] * scale - offsetX;
        top = this.rect[base + 1] * scale - offsetY;
        right = this.rect[base + 2] * scale - offsetX;
        bottom = this.rect[base + 3] * scale - offsetY;
      } else {
        const cx = this.x[index] * scale - offsetX;
        const cy = this.y[index] * scale - offsetY;
        [left, top, right, bottom] = [cx - radius, cy - radius, cx + radius, cy + radius];
      }
      if (right < 0 || bottom < 0 || left > width || top > height) continue; // fuera del canvas

      const style = styles[index];
      ctx.beginPath();
      if (asPixels) {
        ctx.rect(
          left,
          top,
          Math.max(right - left, MIN_PIXEL_SIDE),
          Math.max(bottom - top, MIN_PIXEL_SIDE),
        );
      } else {
        ctx.arc(left + radius, top + radius, radius, 0, Math.PI * 2);
      }
      if (style.fillOpacity > 0) {
        ctx.globalAlpha = style.fillOpacity;
        ctx.fillStyle = style.fillColor;
        ctx.fill();
      }
      if (style.weight > 0 && style.opacity > 0) {
        ctx.globalAlpha = style.opacity;
        ctx.strokeStyle = style.color;
        ctx.lineWidth = style.weight;
        ctx.stroke();
      }
    }
    ctx.globalAlpha = 1;
  }

  private onZoom(): void {
    this.updateTransform(this._map.getCenter(), this._map.getZoom());
  }

  private onZoomAnim(event: L.ZoomAnimEvent): void {
    this.updateTransform(event.center, event.zoom);
  }

  /** Durante la animación del zoom: escala y desplaza el canvas ya dibujado (sin redibujar). */
  private updateTransform(center: L.LatLng, zoom: number): void {
    const map = this._map;
    if (!map || !this.canvas || !this.center) return;
    const scale = map.getZoomScale(zoom, this.zoom);
    const viewHalf = map.getSize().multiplyBy(0.5 + PADDING);
    const currentCenter = map.project(this.center, zoom);
    const newOrigin = (map as unknown as MapInternals)._getNewPixelOrigin(center, zoom);
    const topLeft = viewHalf.multiplyBy(-scale).add(currentCenter).subtract(newOrigin);
    L.DomUtil.setTransform(this.canvas, topLeft, scale);
  }

  /** Índice de la detección dibujada encima bajo `layerPoint` (o -1). */
  private hitTest(layerPoint: L.Point): number {
    const map = this._map;
    const zoom = map.getZoom();
    const scale = 2 ** zoom;
    const origin = map.getPixelOrigin();
    // Punto del ratón en coordenadas de zoom 0.
    const px = (layerPoint.x + origin.x) / scale;
    const py = (layerPoint.y + origin.y) / scale;
    const tolerance = HIT_TOLERANCE / scale;

    if (this.asPixels(zoom)) {
      const minSide = MIN_PIXEL_SIDE / scale;
      for (let index = this.features.length - 1; index >= 0; index -= 1) {
        const base = index * 4;
        const left = this.rect[base] - tolerance;
        const top = this.rect[base + 1] - tolerance;
        const right = Math.max(this.rect[base + 2], this.rect[base] + minSide) + tolerance;
        const bottom = Math.max(this.rect[base + 3], this.rect[base + 1] + minSide) + tolerance;
        if (px >= left && px <= right && py >= top && py <= bottom) return index;
      }
      return -1;
    }
    const radius = (this.settings.pointRadius + HIT_TOLERANCE) / scale;
    const radiusSquared = radius * radius;
    for (let index = this.features.length - 1; index >= 0; index -= 1) {
      const dx = this.x[index] - px;
      const dy = this.y[index] - py;
      if (dx * dx + dy * dy <= radiusSquared) return index;
    }
    return -1;
  }

  // Un hit-test por fotograma como mucho, aunque el ratón genere más eventos.
  private onMouseMove(event: L.LeafletMouseEvent): void {
    this.pendingMove = event;
    if (this.frame) return;
    this.frame = requestAnimationFrame(() => {
      this.frame = 0;
      const move = this.pendingMove;
      if (!move || !this._map) return;
      const index = this.hitTest(move.layerPoint);
      this._map.getContainer().style.cursor = index >= 0 ? "pointer" : "";
      if (index < 0) {
        this.hideTooltip();
        return;
      }
      if (index !== this.hovered) {
        this.hovered = index;
        this.tooltip.setContent(this.settings.tooltip(this.features[index].properties ?? {}));
      }
      this.tooltip.setLatLng(move.latlng);
      if (!this._map.hasLayer(this.tooltip)) {
        this.tooltip.addTo(this._map);
        trackHoveredTooltip(this.tooltip);
      }
    });
  }

  private onClick(event: L.LeafletMouseEvent): void {
    const index = this.hitTest(event.layerPoint);
    if (index < 0) return;
    L.popup()
      .setLatLng(event.latlng)
      .setContent(this.settings.popup(this.features[index].properties ?? {}))
      .openOn(this._map);
  }

  private hideTooltip(): void {
    this.hovered = -1;
    if (this._map) this._map.getContainer().style.cursor = "";
    if (this._map?.hasLayer(this.tooltip)) this.tooltip.remove();
    trackHoveredTooltip(null, this.tooltip);
  }
}

function featureLatLng(feature: FireFeature): L.LatLng {
  const geometry = feature.geometry;
  if (geometry.type !== "Point") throw new Error("Las detecciones deben venir como puntos");
  return L.latLng(geometry.coordinates[1], geometry.coordinates[0]);
}
