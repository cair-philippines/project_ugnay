import { useMemo, useCallback } from "react";
import DeckGL from "@deck.gl/react";
import { Map } from "react-map-gl/maplibre";
import { ScatterplotLayer, ArcLayer, GeoJsonLayer } from "@deck.gl/layers";

const INITIAL_VIEW_STATE = {
  latitude: 12.5,
  longitude: 122.0,
  zoom: 5.5,
  pitch: 0,
  bearing: 0,
};

const MAP_STYLE =
  "https://basemaps.cartocdn.com/gl/positron-gl-style/style.json";

// Color scales
const SECTOR_COLORS = {
  public: [59, 130, 246],    // blue
  private: [249, 115, 22],   // orange
};

function metricToColor(value, min, max) {
  if (value == null || isNaN(value)) return [200, 200, 200, 100];
  const t = Math.min(1, Math.max(0, (value - min) / (max - min || 1)));
  // Green (good) → Yellow → Red (bad)
  const r = Math.round(t < 0.5 ? t * 2 * 255 : 255);
  const g = Math.round(t < 0.5 ? 255 : (1 - (t - 0.5) * 2) * 255);
  return [r, g, 0, 180];
}

export default function MapView({
  schools,
  aggregations,
  boundaries,
  metric,
  selectedSchool,
  neighbors,
  onSchoolClick,
}) {
  // Compute metric range for choropleth color scale
  const metricRange = useMemo(() => {
    if (!aggregations || aggregations.length === 0) return { min: 0, max: 1 };
    const values = aggregations
      .map((a) => a[metric])
      .filter((v) => v != null && !isNaN(v));
    return {
      min: Math.min(...values),
      max: Math.max(...values),
    };
  }, [aggregations, metric]);

  // Build choropleth lookup: ADM3_PCODE → metric value
  // Uses PSGC code matching: psgc_municity (10-digit) → ADM3_PCODE (PH + 7-digit)
  const choroplethLookup = useMemo(() => {
    if (!aggregations) return {};
    const lookup = {};
    for (const agg of aggregations) {
      if (agg.psgc_municity) {
        // Convert 10-digit PSGC to ADM3_PCODE format: "0102802000" → "PH0102802"
        const psgc = String(agg.psgc_municity).padStart(10, "0");
        const admCode = "PH" + psgc.slice(0, 7);
        lookup[admCode] = agg[metric];
      }
    }
    return lookup;
  }, [aggregations, metric]);

  const handleClick = useCallback(
    (info) => {
      if (info.object && info.object.school_id) {
        onSchoolClick(info.object.school_id);
      }
    },
    [onSchoolClick]
  );

  const layers = useMemo(() => {
    const result = [];

    // 1. Choropleth layer (GeoJSON boundaries colored by metric)
    if (boundaries && boundaries.features) {
      result.push(
        new GeoJsonLayer({
          id: "choropleth",
          data: boundaries,
          filled: true,
          stroked: true,
          getFillColor: (f) => {
            // Match by PSGC code (ADM3_PCODE)
            const value = choroplethLookup[f.properties.ADM3_PCODE];
            return metricToColor(value, metricRange.min, metricRange.max);
          },
          getLineColor: [100, 100, 100, 80],
          lineWidthMinPixels: 0.5,
          pickable: true,
          updateTriggers: {
            getFillColor: [choroplethLookup, metricRange],
          },
        })
      );
    }

    // 2. School dots
    if (schools && schools.length > 0) {
      result.push(
        new ScatterplotLayer({
          id: "schools",
          data: schools,
          getPosition: (d) => [d.longitude, d.latitude],
          getFillColor: (d) =>
            d.school_id === selectedSchool?.school_id
              ? [220, 38, 38, 255]  // red for selected
              : SECTOR_COLORS[d.sector] || [128, 128, 128],
          getRadius: (d) =>
            d.school_id === selectedSchool?.school_id ? 800 : 300,
          radiusMinPixels: 2,
          radiusMaxPixels: 12,
          pickable: true,
          onClick: handleClick,
          updateTriggers: {
            getFillColor: [selectedSchool?.school_id],
            getRadius: [selectedSchool?.school_id],
          },
        })
      );
    }

    // 3. Neighbor arcs (when a school is selected)
    if (selectedSchool && neighbors && neighbors.neighbors) {
      result.push(
        new ArcLayer({
          id: "neighbor-arcs",
          data: neighbors.neighbors,
          getSourcePosition: () => [
            selectedSchool.longitude,
            selectedSchool.latitude,
          ],
          getTargetPosition: (d) => [d.target_lon, d.target_lat],
          getSourceColor: [220, 38, 38, 180],
          getTargetColor: (d) => {
            // Green (close) → Red (far)
            const dist = d.road_distance_m / 1000;
            const t = Math.min(1, dist / 20);
            return [
              Math.round(t * 220),
              Math.round((1 - t) * 180),
              0,
              150,
            ];
          },
          getWidth: 2,
          pickable: true,
        })
      );
    }

    return result;
  }, [
    schools,
    boundaries,
    choroplethLookup,
    metricRange,
    selectedSchool,
    neighbors,
    handleClick,
  ]);

  return (
    <DeckGL
      initialViewState={INITIAL_VIEW_STATE}
      controller={true}
      layers={layers}
      getTooltip={({ object }) => {
        if (!object) return null;
        // School tooltip
        if (object.school_id) {
          return {
            html: `<b>${object.school_name || object.school_id}</b><br/>
              ${object.sector} | ${object.municipality}<br/>
              Isolation: ${(object.isolation_score ?? 0).toFixed(3)}<br/>
              Neighbors (10km): ${object.n_neighbors_10km ?? 0}`,
          };
        }
        // Neighbor arc tooltip
        if (object.target_id) {
          return {
            html: `<b>${object.target_name || object.target_id}</b><br/>
              ${(object.road_distance_m / 1000).toFixed(1)} km driving`,
          };
        }
        // Boundary tooltip
        if (object.properties) {
          const p = object.properties;
          const value = choroplethLookup[p.ADM3_PCODE];
          return {
            html: `<b>${p.ADM3_EN || p.ADM2_EN}</b><br/>
              ${p.ADM2_EN}, ${p.ADM1_EN}<br/>
              ${metric}: ${value != null ? value.toFixed(2) : "N/A"}`,
          };
        }
        return null;
      }}
    >
      <Map mapStyle={MAP_STYLE} />
    </DeckGL>
  );
}
