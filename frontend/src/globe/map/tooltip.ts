// Tooltip que sigue al cursor sobre el mapa; con Mayús se deja fijo (varios a la vez) y Esc
// quita los fijados.
import * as maplibregl from "maplibre-gl";

import { map } from "./map.js";

const TIP_OPTIONS = { closeButton: false, closeOnClick: false, offset: 12, maxWidth: "280px" };

const tip = new maplibregl.Popup({ ...TIP_OPTIONS, className: "globe-tip" });
let tipContent: { html: string; lngLat: maplibregl.LngLat } | null = null;
const pinnedTips: maplibregl.Popup[] = [];

export function showTip(html: string, lngLat: maplibregl.LngLat): void {
  tipContent = { html, lngLat };
  tip.setLngLat(lngLat).setHTML(html).addTo(map);
}

export function hideTip(): void {
  tip.remove();
  tipContent = null;
}

export function initTooltipPinning(): void {
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      pinnedTips.splice(0).forEach((popup) => popup.remove());
    } else if (event.key === "Shift" && !event.repeat && tipContent) {
      pinnedTips.push(
        new maplibregl.Popup({ ...TIP_OPTIONS, className: "globe-tip pinned" })
          .setLngLat(tipContent.lngLat)
          .setHTML(tipContent.html)
          .addTo(map),
      );
    }
  });
}
