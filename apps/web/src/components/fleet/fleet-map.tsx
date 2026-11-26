"use client";

import "maplibre-gl/dist/maplibre-gl.css";
import type { GeoJSONSource, Map as MlMap, MapLayerMouseEvent, Popup } from "maplibre-gl";
import { useTheme } from "next-themes";
import { useEffect, useRef, useState } from "react";
import { GROUP_COLORS, classGroup } from "@/lib/fleet";
import { cn } from "@/lib/utils";
import type { Port, TrackResponse, VesselFeature } from "@/lib/types";

const STYLE = {
  light: process.env.NEXT_PUBLIC_MAP_STYLE_LIGHT ?? "https://tiles.openfreemap.org/styles/positron",
  dark: process.env.NEXT_PUBLIC_MAP_STYLE_DARK ?? "https://tiles.openfreemap.org/styles/dark",
};
const SURFACE = { light: "#fcfcfb", dark: "#1a1a19" };
const PORT_MIN_ZOOM = 4;

type Mode = "light" | "dark";

export interface Viewport {
  bbox: [number, number, number, number];
  zoom: number;
}

export interface FleetMapProps {
  vessels: VesselFeature[];
  ports?: Port[];
  track?: TrackResponse | null;
  selectedId?: string | null;
  focus?: { lon: number; lat: number; zoom?: number; key: string } | null;
  /** Fit the view to a bounding box [w, s, e, n]; change `key` to re-apply. */
  fitTo?: { bbox: [number, number, number, number]; key: string } | null;
  onSelect?: (id: string | null) => void;
  onViewportChange?: (v: Viewport) => void;
  initialView?: { center: [number, number]; zoom: number };
  className?: string;
}

function vesselCollection(vessels: VesselFeature[]) {
  return {
    type: "FeatureCollection" as const,
    features: vessels.map((f) => ({
      ...f,
      properties: { ...f.properties, group: classGroup(f.properties.vessel_class) },
    })),
  };
}

function portCollection(ports: Port[]) {
  return {
    type: "FeatureCollection" as const,
    features: ports.map((p) => ({
      type: "Feature" as const,
      id: p.id,
      geometry: { type: "Point" as const, coordinates: [p.lon, p.lat] },
      properties: { id: p.id, name: p.name, unlocode: p.unlocode, in_port: p.vessels_in_port },
    })),
  };
}

function trackCollection(track: TrackResponse | null | undefined) {
  return {
    type: "FeatureCollection" as const,
    features: track
      ? track.geometry.coordinates.map((line, i) => ({
          type: "Feature" as const,
          id: i,
          geometry: { type: "LineString" as const, coordinates: line },
          properties: {},
        }))
      : [],
  };
}

function addLayers(map: MlMap, mode: Mode) {
  const colors = GROUP_COLORS[mode];
  const empty = { type: "FeatureCollection" as const, features: [] };
  map.addSource("track", { type: "geojson", data: empty });
  map.addSource("ports", { type: "geojson", data: empty });
  map.addSource("vessels", { type: "geojson", data: empty, promoteId: "id" });

  map.addLayer({
    id: "track",
    type: "line",
    source: "track",
    layout: { "line-cap": "round", "line-join": "round" },
    paint: { "line-color": mode === "dark" ? "#f0efec" : "#0b0b0b", "line-width": 2, "line-opacity": 0.7 },
  });
  map.addLayer({
    id: "ports",
    type: "circle",
    source: "ports",
    minzoom: PORT_MIN_ZOOM,
    // Ports are hollow rings so they never read as (filled) vessel markers.
    paint: {
      "circle-radius": ["case", [">", ["get", "in_port"], 0], 5.5, 4],
      "circle-color": "rgba(0,0,0,0)",
      "circle-stroke-color": mode === "dark" ? "#c3c2b7" : "#52514e",
      "circle-stroke-width": ["case", [">", ["get", "in_port"], 0], 2, 1.25],
    },
  });
  map.addLayer({
    id: "vessel-selected",
    type: "circle",
    source: "vessels",
    filter: ["==", ["get", "id"], ""],
    paint: {
      "circle-radius": ["interpolate", ["linear"], ["zoom"], 1, 8, 8, 12],
      "circle-color": "rgba(0,0,0,0)",
      "circle-stroke-color": mode === "dark" ? "#f0efec" : "#0b0b0b",
      "circle-stroke-width": 2,
    },
  });
  map.addLayer({
    id: "vessels",
    type: "circle",
    source: "vessels",
    paint: {
      "circle-radius": ["interpolate", ["linear"], ["zoom"], 1, 4, 6, 5.5, 10, 7],
      "circle-color": [
        "match",
        ["get", "group"],
        "cargo", colors.cargo,
        "tanker", colors.tanker,
        "passenger", colors.passenger,
        colors.other,
      ],
      // Age is a second channel: stale positions fade so they never read as current.
      "circle-opacity": ["match", ["get", "freshness"], "live", 1, "recent", 0.7, 0.35],
      "circle-stroke-color": SURFACE[mode],
      "circle-stroke-width": 1.5,
    },
  });
}

export function FleetMap({
  vessels,
  ports = [],
  track,
  selectedId,
  focus,
  onSelect,
  onViewportChange,
  initialView = { center: [40, 20], zoom: 1.6 },
  fitTo,
  className,
}: FleetMapProps) {
  const container = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MlMap | null>(null);
  const popupRef = useRef<Popup | null>(null);
  const callbacks = useRef({ onSelect, onViewportChange });
  callbacks.current = { onSelect, onViewportChange };
  const [styleVersion, setStyleVersion] = useState(0);
  const [failed, setFailed] = useState<string | null>(null);
  const { resolvedTheme } = useTheme();
  const mode: Mode = resolvedTheme === "dark" ? "dark" : "light";
  const modeRef = useRef(mode);
  const fitRef = useRef(fitTo);
  fitRef.current = fitTo;

  // Create the map once.
  useEffect(() => {
    let cancelled = false;
    let map: MlMap | undefined;
    import("maplibre-gl")
      .then(({ default: maplibregl }) => {
        if (cancelled || !container.current) return;
        map = new maplibregl.Map({
          container: container.current,
          style: STYLE[modeRef.current],
          center: initialView.center,
          zoom: initialView.zoom,
          attributionControl: { compact: true },
          renderWorldCopies: true,
        });
        mapRef.current = map;
        map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
        popupRef.current = new maplibregl.Popup({ closeButton: false, closeOnClick: false, offset: 10 });

        map.on("style.load", () => {
          addLayers(map!, modeRef.current);
          setStyleVersion((v) => v + 1);
        });
        map.on("error", (e) => {
          // Basemap tiles failing must not hide vessel data; report quietly.
          if (String(e.error?.message ?? "").includes("style")) setFailed("Basemap unavailable");
        });
        const emitViewport = () => {
          const b = map!.getBounds();
          callbacks.current.onViewportChange?.({
            bbox: [
              Math.max(-180, b.getWest()),
              Math.max(-90, b.getSouth()),
              Math.min(180, b.getEast()),
              Math.min(90, b.getNorth()),
            ],
            zoom: map!.getZoom(),
          });
        };
        map.on("moveend", emitViewport);
        map.on("load", () => {
          // Apply a region requested before the map finished loading (e.g. from the URL).
          const fit = fitRef.current;
          if (fit) {
            const [w, s, e, n] = fit.bbox;
            map!.fitBounds([[w, s], [e, n]], { padding: 24, duration: 0 });
          }
          emitViewport();
        });

        map.on("click", "vessels", (e: MapLayerMouseEvent) => {
          const id = e.features?.[0]?.properties?.id as string | undefined;
          if (id) callbacks.current.onSelect?.(id);
        });
        map.on("click", (e) => {
          const hit = map!.queryRenderedFeatures(e.point, { layers: ["vessels"] });
          if (!hit.length) callbacks.current.onSelect?.(null);
        });
        const hover = (layer: "vessels" | "ports") => (e: MapLayerMouseEvent) => {
          const f = e.features?.[0];
          if (!f) return;
          map!.getCanvas().style.cursor = "pointer";
          const p = f.properties ?? {};
          const label =
            layer === "vessels"
              ? `<strong>${escapeHtml(String(p.name ?? `MMSI ${p.mmsi}`))}</strong><br/>${escapeHtml(
                  String(p.freshness),
                )} · ${p.data_mode === "synthetic" ? "synthetic data" : escapeHtml(String(p.source ?? ""))}`
              : `<strong>${escapeHtml(String(p.name))}</strong><br/>${escapeHtml(String(p.unlocode ?? ""))}${
                  Number(p.in_port) > 0 ? ` · ${p.in_port} in port` : ""
                }`;
          popupRef.current?.setLngLat(e.lngLat).setHTML(label).addTo(map!);
        };
        const leave = () => {
          map!.getCanvas().style.cursor = "";
          popupRef.current?.remove();
        };
        map.on("mousemove", "vessels", hover("vessels"));
        map.on("mousemove", "ports", hover("ports"));
        map.on("mouseleave", "vessels", leave);
        map.on("mouseleave", "ports", leave);
      })
      .catch(() => setFailed("Map failed to load (WebGL unavailable?)"));
    return () => {
      cancelled = true;
      map?.remove();
      mapRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Theme switch: swap the basemap; layers are re-added on style.load.
  useEffect(() => {
    if (modeRef.current === mode) return;
    modeRef.current = mode;
    // diff: false forces a full reload so `style.load` fires and our layers are re-added;
    // a diffed swap can drop custom layers without emitting it.
    mapRef.current?.setStyle(STYLE[mode], { diff: false });
  }, [mode]);

  useEffect(() => {
    (mapRef.current?.getSource("vessels") as GeoJSONSource | undefined)?.setData(vesselCollection(vessels));
  }, [vessels, styleVersion]);

  useEffect(() => {
    (mapRef.current?.getSource("ports") as GeoJSONSource | undefined)?.setData(portCollection(ports));
  }, [ports, styleVersion]);

  useEffect(() => {
    (mapRef.current?.getSource("track") as GeoJSONSource | undefined)?.setData(trackCollection(track));
  }, [track, styleVersion]);

  useEffect(() => {
    const map = mapRef.current;
    if (map?.getLayer("vessel-selected")) {
      map.setFilter("vessel-selected", ["==", ["get", "id"], selectedId ?? ""]);
    }
  }, [selectedId, styleVersion]);

  useEffect(() => {
    if (focus && mapRef.current) {
      mapRef.current.easeTo({ center: [focus.lon, focus.lat], zoom: Math.max(mapRef.current.getZoom(), focus.zoom ?? 6) });
    }
  }, [focus?.key]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const map = mapRef.current;
    if (fitTo && map?.loaded()) {
      const [w, s, e, n] = fitTo.bbox;
      map.fitBounds([[w, s], [e, n]], { padding: 24, duration: 800 });
    }
  }, [fitTo?.key]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    // `relative` is only a default: tailwind-merge lets a caller's `absolute inset-0` win.
    // (An inline position style here once overrode it and collapsed the map to 0 px.)
    <div className={cn("relative", className)}>
      <div ref={container} className="absolute inset-0" data-testid="fleet-map" />
      {failed ? (
        <div role="status" className="absolute bottom-2 left-2 rounded bg-card/90 px-2 py-1 text-xs text-muted-foreground">
          {failed}
        </div>
      ) : null}
    </div>
  );
}

function escapeHtml(s: string): string {
  return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);
}
