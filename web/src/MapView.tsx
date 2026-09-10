import maplibregl, { Map as MLMap, MapMouseEvent } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { useEffect, useRef } from "react";

// Free OSM raster basemap — no API key. Swap for a vector style later.
const BASE_STYLE: maplibregl.StyleSpecification = {
  version: 8,
  sources: {
    osm: {
      type: "raster",
      tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
      tileSize: 256,
      attribution: "&copy; OpenStreetMap contributors",
    },
  },
  layers: [{ id: "osm", type: "raster", source: "osm" }],
};

const SRC = "parcels";

const ASM_SRC = "assemblages";
const HL_SRC = "highlight";

// score gradient (yellow -> red) vs. a flat fill for filter-first browsing
const SCORE_FILL: maplibregl.ExpressionSpecification = [
  "interpolate", ["linear"], ["get", "opportunity_score"],
  0, "#fee08b", 50, "#fdae61", 70, "#f46d43", 90, "#d73027",
];
const FLAT_FILL = "#4f86c6";

interface Props {
  data: GeoJSON.FeatureCollection | null;
  assemblages: GeoJSON.FeatureCollection | null;
  scoringOn?: boolean;                                   // color by score vs. flat
  onSelect: (parcelId: string) => void;
  focus?: { lng: number; lat: number; key: number } | null;  // fly to a parcel
  highlight?: GeoJSON.FeatureCollection | null;  // a looked-up parcel to outline
  fitBounds?: [number, number, number, number] | null;  // [w,s,e,n] to fit (region jump)
  onViewport?: (bbox: string, zoom: number) => void;     // fires on pan/zoom settle
}

export default function MapView({ data, assemblages, scoringOn = false, onSelect,
                                 focus = null, highlight = null, fitBounds = null,
                                 onViewport }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MLMap | null>(null);
  const loadedRef = useRef(false);
  // keep the latest onViewport in a ref so the moveend handler isn't re-bound
  const onViewportRef = useRef(onViewport);
  onViewportRef.current = onViewport;
  const scoringOnRef = useRef(scoringOn);
  scoringOnRef.current = scoringOn;

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;
    const map = new maplibregl.Map({
      container: containerRef.current,
      // fresh copy per map: MapLibre mutates the style spec it's given, and
      // StrictMode's double-mount otherwise hands the second map a spec the
      // first (removed) map already consumed — style load then hangs silently
      style: structuredClone(BASE_STYLE),
      center: [-84.51, 39.11], // Cincinnati
      zoom: 11,
    });
    map.addControl(new maplibregl.NavigationControl(), "top-right");

    // style.load (not load): parcel layers must appear even when basemap tile
    // fetches are slow or blocked — the data layer shouldn't hostage on OSM.
    map.once("style.load", () => {
      map.addSource(SRC, { type: "geojson", data: { type: "FeatureCollection", features: [] } });
      // fill: a score gradient when scoring is on, a flat colour when the realtor
      // is just browsing their filters (the score isn't guiding them then)
      map.addLayer({
        id: "parcel-fill",
        type: "fill",
        source: SRC,
        paint: {
          "fill-color": scoringOnRef.current ? SCORE_FILL : FLAT_FILL,
          "fill-opacity": 0.65,
        },
      });
      map.addLayer({
        id: "parcel-line",
        type: "line",
        source: SRC,
        paint: { "line-color": "#7f1d1d", "line-width": 0.6 },
      });
      // assemblage outlines: one dashed purple boundary around the union of a
      // same-owner cluster, drawn OVER the member parcels so the assembled
      // site reads as one opportunity while its lots stay individually visible
      map.addSource(ASM_SRC, { type: "geojson", data: { type: "FeatureCollection", features: [] } });
      map.addLayer({
        id: "assemblage-line",
        type: "line",
        source: ASM_SRC,
        paint: {
          "line-color": "#7c3aed",
          "line-width": 2.5,
          "line-dasharray": [2, 1.5],
        },
      });
      // looked-up parcel: a bold cyan outline + translucent fill drawn on top,
      // so a searched lot is unmistakable even if it's outside the current slice
      map.addSource(HL_SRC, { type: "geojson", data: { type: "FeatureCollection", features: [] } });
      map.addLayer({
        id: "highlight-fill", type: "fill", source: HL_SRC,
        paint: { "fill-color": "#06b6d4", "fill-opacity": 0.25 },
      });
      map.addLayer({
        id: "highlight-line", type: "line", source: HL_SRC,
        paint: { "line-color": "#0891b2", "line-width": 3 },
      });
      map.on("click", "parcel-fill", (e: MapMouseEvent & { features?: maplibregl.MapGeoJSONFeature[] }) => {
        const pid = e.features?.[0]?.properties?.parcel_id;
        if (pid) onSelect(String(pid));
      });
      map.on("mouseenter", "parcel-fill", () => (map.getCanvas().style.cursor = "pointer"));
      map.on("mouseleave", "parcel-fill", () => (map.getCanvas().style.cursor = ""));
      // report the viewport whenever the map settles, so the app can load every
      // parcel currently in view (not just the global top-N by score)
      const report = () => {
        const b = map.getBounds();
        onViewportRef.current?.(
          `${b.getWest()},${b.getSouth()},${b.getEast()},${b.getNorth()}`, map.getZoom());
      };
      map.on("moveend", report);
      report();  // initial viewport
      loadedRef.current = true;
      if (dataRef.current) {
        (map.getSource(SRC) as maplibregl.GeoJSONSource).setData(dataRef.current);
      }
      if (asmRef.current) {
        (map.getSource(ASM_SRC) as maplibregl.GeoJSONSource).setData(asmRef.current);
      }
      if (hlRef.current) {
        (map.getSource(HL_SRC) as maplibregl.GeoJSONSource).setData(hlRef.current);
      }
    });
    mapRef.current = map;
    // dev handle for debugging / driving the map from the console; cleared on
    // unmount so StrictMode's mount-unmount-mount never leaves a ghost handle
    (window as unknown as { __map?: MLMap }).__map = map;
    return () => {
      const w = window as unknown as { __map?: MLMap };
      if (w.__map === map) delete w.__map;
      map.remove(); mapRef.current = null; loadedRef.current = false;
    };
  }, [onSelect]);

  // keep latest data available for the load handler race
  const dataRef = useRef<GeoJSON.FeatureCollection | null>(null);
  const asmRef = useRef<GeoJSON.FeatureCollection | null>(null);
  const hlRef = useRef<GeoJSON.FeatureCollection | null>(null);
  useEffect(() => {
    dataRef.current = data;
    const map = mapRef.current;
    if (map && loadedRef.current && data) {
      (map.getSource(SRC) as maplibregl.GeoJSONSource).setData(data);
    }
  }, [data]);
  useEffect(() => {
    asmRef.current = assemblages;
    const map = mapRef.current;
    if (map && loadedRef.current && assemblages) {
      (map.getSource(ASM_SRC) as maplibregl.GeoJSONSource).setData(assemblages);
    }
  }, [assemblages]);

  // recolor the fill when the scoring toggle flips
  useEffect(() => {
    const map = mapRef.current;
    if (map && loadedRef.current && map.getLayer("parcel-fill")) {
      map.setPaintProperty("parcel-fill", "fill-color", scoringOn ? SCORE_FILL : FLAT_FILL);
    }
  }, [scoringOn]);

  useEffect(() => {
    hlRef.current = highlight;
    const map = mapRef.current;
    if (map && loadedRef.current) {
      (map.getSource(HL_SRC) as maplibregl.GeoJSONSource)?.setData(
        highlight ?? { type: "FeatureCollection", features: [] });
    }
  }, [highlight]);

  // fly to a picked parcel (top-list click)
  useEffect(() => {
    const map = mapRef.current;
    if (map && focus) map.flyTo({ center: [focus.lng, focus.lat], zoom: 17, duration: 900 });
  }, [focus]);

  // fit to a region's bounding box (region jump); a moveend then loads the
  // parcels in view. Keyed on the actual values so re-selecting refits.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !fitBounds) return;
    const [w, s, e, n] = fitBounds;
    map.fitBounds([[w, s], [e, n]], { padding: 40, duration: 800 });
  }, [fitBounds]);

  return <div ref={containerRef} className="map" />;
}
