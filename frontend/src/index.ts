import L from "leaflet";
import "leaflet/dist/leaflet.css";

const mapContainer = document.querySelector<HTMLElement>(".map");
if (mapContainer) {
  mapContainer.id = "map";
  const map = L.map(mapContainer).setView([39.7, -4.85], 13);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: "&copy; OpenStreetMap contributors",
  }).addTo(map);

  const center = L.latLng(39.7, -4.85);
  const halfSideMeters = 5000;
  const latOffset = halfSideMeters / 111320;
  const lngOffset = halfSideMeters / (111320 * Math.cos((center.lat * Math.PI) / 180));
  const area = L.polygon([
    [center.lat - latOffset, center.lng - lngOffset],
    [center.lat - latOffset, center.lng + lngOffset],
    [center.lat + latOffset, center.lng + lngOffset],
    [center.lat + latOffset, center.lng - lngOffset],
  ], { color: "#d17b35", weight: 2, fillColor: "#e9a45d", fillOpacity: 0.35 }).addTo(map);
  area.bindPopup("Area de estudio: 2 x 2 km (4 km2)");

  const coordinates = new L.Control({ position: "topright" });
  coordinates.onAdd = () => {
    const element = L.DomUtil.create("div", "coordinates-control");
    element.innerHTML = "Mueve el cursor sobre el mapa";
    return element;
  };
  coordinates.addTo(map);

  map.on("mousemove", (event: L.LeafletMouseEvent) => {
    const element = document.querySelector<HTMLElement>(".coordinates-control");
    if (element) element.innerHTML = `Latitud: <b>${event.latlng.lat.toFixed(5)}°</b><br>Longitud: <b>${event.latlng.lng.toFixed(5)}°</b><br>Altitud: disponible al consultar el punto`;
  });
}
