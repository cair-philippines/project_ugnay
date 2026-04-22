import { useState, useEffect, useCallback } from "react";
import { useSchoolData } from "./hooks/useSchoolData";
import MapView from "./components/MapView";
import FilterBar from "./components/FilterBar";
import SchoolPanel from "./components/SchoolPanel";
import MetricLegend from "./components/MetricLegend";
import StatsBar from "./components/StatsBar";

const METRIC_OPTIONS = [
  { value: "mean_isolation_score", label: "Isolation Score" },
  { value: "mean_feeder_isolation", label: "Feeder Isolation" },
  { value: "pct_private_desert", label: "Private Desert %" },
  { value: "pct_esc_desert", label: "ESC Desert %" },
  { value: "pct_jhs_desert", label: "JHS Desert %" },
  { value: "pct_shs_desert", label: "SHS Desert %" },
  { value: "mean_nearest_private_km", label: "Avg Nearest Private (km)" },
  { value: "mean_nearest_esc_km", label: "Avg Nearest ESC (km)" },
];

export default function App() {
  const {
    stats,
    filters,
    schools,
    aggregations,
    boundaries,
    selectedSchool,
    neighbors,
    loading,
    loadSchools,
    loadFilters,
    loadAggregations,
    loadBoundaries,
    selectSchool,
  } = useSchoolData();

  const [region, setRegion] = useState("");
  const [province, setProvince] = useState("");
  const [municipality, setMunicipality] = useState("");
  const [metric, setMetric] = useState("mean_isolation_score");
  const [boundaryLevel, setBoundaryLevel] = useState("municipal");

  // Load boundaries and aggregations on mount
  useEffect(() => {
    loadBoundaries("municipal");
    loadAggregations({ level: "municipal" });
    loadSchools({ limit: 60000 });
  }, []);

  // Update filters on region/province change
  useEffect(() => {
    loadFilters({ region: region || undefined, province: province || undefined });
  }, [region, province]);

  // Update aggregations when filters change
  useEffect(() => {
    loadAggregations({
      level: boundaryLevel,
      region: region || undefined,
      province: province || undefined,
    });
  }, [region, province, boundaryLevel]);

  // Update schools when filters change
  useEffect(() => {
    loadSchools({
      region: region || undefined,
      province: province || undefined,
      municipality: municipality || undefined,
      limit: 60000,
    });
  }, [region, province, municipality]);

  const handleRegionChange = useCallback((val) => {
    setRegion(val);
    setProvince("");
    setMunicipality("");
    selectSchool(null);
  }, [selectSchool]);

  const handleProvinceChange = useCallback((val) => {
    setProvince(val);
    setMunicipality("");
    selectSchool(null);
  }, [selectSchool]);

  const handleMunicipalityChange = useCallback((val) => {
    setMunicipality(val);
    selectSchool(null);
  }, [selectSchool]);

  const handleSchoolClick = useCallback((schoolId) => {
    selectSchool(schoolId);
  }, [selectSchool]);

  const handleClosePanel = useCallback(() => {
    selectSchool(null);
  }, [selectSchool]);

  return (
    <div className="flex flex-col h-screen w-screen">
      {/* Top bar */}
      <div className="flex items-center gap-4 px-4 py-2 bg-white border-b border-gray-200 z-10">
        <h1 className="text-lg font-semibold text-gray-800 whitespace-nowrap">
          Ugnay
        </h1>
        <FilterBar
          filters={filters}
          region={region}
          province={province}
          municipality={municipality}
          onRegionChange={handleRegionChange}
          onProvinceChange={handleProvinceChange}
          onMunicipalityChange={handleMunicipalityChange}
        />
        <select
          className="px-2 py-1 border border-gray-300 rounded text-sm"
          value={metric}
          onChange={(e) => setMetric(e.target.value)}
        >
          {METRIC_OPTIONS.map((opt) => (
            <option key={opt.value} value={opt.value}>
              {opt.label}
            </option>
          ))}
        </select>
        {stats && <StatsBar stats={stats} />}
      </div>

      {/* Map + side panel */}
      <div className="flex flex-1 overflow-hidden">
        <div className="flex-1 relative">
          <MapView
            schools={schools}
            aggregations={aggregations}
            boundaries={boundaries}
            metric={metric}
            selectedSchool={selectedSchool}
            neighbors={neighbors}
            onSchoolClick={handleSchoolClick}
          />
          <MetricLegend metric={metric} />
        </div>

        {selectedSchool && (
          <SchoolPanel
            school={selectedSchool}
            neighbors={neighbors}
            onClose={handleClosePanel}
          />
        )}
      </div>
    </div>
  );
}
