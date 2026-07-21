import "leaflet/dist/leaflet.css";
import L from "leaflet";
import { useEffect, useState } from "react";
import { MapContainer, Marker, Popup, TileLayer, useMap } from "react-leaflet";
import { Link } from "react-router-dom";

import { apiFetch } from "../lib/api";
import { cn } from "../lib/cn";

// All markers use custom divIcons below, so Leaflet's default image icons
// (which bundlers break and which would pull from a CDN) are never needed.

function buildingIcon(count) {
  return L.divIcon({
    html: `<div style="
      background: var(--accent-tag, #8a1d45);
      color: white;
      border-radius: 50%;
      width: 32px; height: 32px;
      display: flex; align-items: center; justify-content: center;
      font-size: 12px; font-weight: 700;
      border: 2px solid white;
      box-shadow: 0 2px 6px rgba(0,0,0,0.3);
    ">${count}</div>`,
    className: "",
    iconSize: [32, 32],
    iconAnchor: [16, 16],
  });
}

function RecenterButton({ center }) {
  const map = useMap();
  return (
    <button
      onClick={() => map.setView(center, 15)}
      className="absolute bottom-4 right-4 z-[500] rounded-lg bg-[color:var(--bg-surface)] px-3 py-1.5 text-xs font-medium text-[color:var(--color-ink)] shadow border border-[color:var(--color-line-strong)]"
    >
      Re-centre
    </button>
  );
}

export default function MapView({ className }) {
  const [pins, setPins] = useState([]);
  const [loading, setLoading] = useState(true);
  const [center, setCenter] = useState([34.02, -118.28]); // fallback: LA

  useEffect(() => {
    apiFetch("/listings/map-data")
      .then((data) => {
        setPins(data.pins ?? []);
        const first = data.pins?.find((p) => p.lat != null);
        if (first) setCenter([first.lat, first.lng]);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return (
      <div className={cn("flex items-center justify-center bg-[color:var(--bg-surface-2)] rounded-2xl", className)}>
        <div className="h-6 w-6 animate-spin rounded-full border-2 border-[color:var(--color-tag)] border-t-transparent" />
      </div>
    );
  }

  const mappable = pins.filter((p) => p.lat != null);
  const unmapped = pins.filter((p) => p.lat == null);

  return (
    <div className={cn("relative rounded-2xl overflow-hidden border border-[color:var(--color-line-strong)]", className)}>
      <MapContainer
        center={center}
        zoom={15}
        scrollWheelZoom={false}
        style={{ height: "100%", width: "100%" }}
        className="z-0"
      >
        <TileLayer
          attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
          url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
        />
        {mappable.map((pin) => (
          <Marker key={pin.building} position={[pin.lat, pin.lng]} icon={buildingIcon(pin.count)}>
            <Popup>
              <div className="min-w-[180px]">
                <p className="font-semibold text-sm mb-1">{pin.building}</p>
                <p className="text-xs text-gray-500 mb-2">{pin.count} available item{pin.count !== 1 ? "s" : ""}</p>
                <ul className="space-y-1">
                  {pin.items.slice(0, 3).map((item) => (
                    <li key={item.id}>
                      <Link
                        to={`/listings/${item.id}`}
                        className="text-xs font-semibold block truncate hover:underline"
                        style={{ color: "var(--accent-tag, #8a1d45)" }}
                      >
                        {item.title}
                      </Link>
                    </li>
                  ))}
                </ul>
                {pin.count > 3 && (
                  <p className="text-xs text-gray-400 mt-1">+{pin.count - 3} more</p>
                )}
              </div>
            </Popup>
          </Marker>
        ))}
        <RecenterButton center={center} />
      </MapContainer>

      {unmapped.length > 0 && (
        <div className="absolute bottom-0 left-0 right-0 z-[500] bg-black/60 px-3 py-1.5 text-xs text-white/80">
          {unmapped.length} building{unmapped.length !== 1 ? "s" : ""} without coordinates — add them in campus settings
        </div>
      )}
    </div>
  );
}
