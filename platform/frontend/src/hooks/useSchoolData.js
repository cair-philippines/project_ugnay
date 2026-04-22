import { useState, useEffect, useCallback } from "react";

const API = "/api";

export function useSchoolData() {
  const [stats, setStats] = useState(null);
  const [filters, setFilters] = useState({ regions: [] });
  const [schools, setSchools] = useState([]);
  const [totalSchools, setTotalSchools] = useState(0);
  const [aggregations, setAggregations] = useState([]);
  const [boundaries, setBoundaries] = useState(null);
  const [selectedSchool, setSelectedSchool] = useState(null);
  const [neighbors, setNeighbors] = useState(null);
  const [loading, setLoading] = useState(false);

  // Load stats on mount
  useEffect(() => {
    fetch(`${API}/stats`).then((r) => r.json()).then(setStats);
    fetch(`${API}/filters`).then((r) => r.json()).then(setFilters);
  }, []);

  // Load schools for a region/province/municipality
  const loadSchools = useCallback(async ({ region, province, municipality, sector, limit = 60000 } = {}) => {
    setLoading(true);
    const params = new URLSearchParams();
    if (region) params.set("region", region);
    if (province) params.set("province", province);
    if (municipality) params.set("municipality", municipality);
    if (sector) params.set("sector", sector);
    params.set("limit", limit);

    const res = await fetch(`${API}/schools?${params}`);
    const data = await res.json();
    setSchools(data.schools);
    setTotalSchools(data.total);
    setLoading(false);
    return data;
  }, []);

  // Load filter options (cascading)
  const loadFilters = useCallback(async ({ region, province } = {}) => {
    const params = new URLSearchParams();
    if (region) params.set("region", region);
    if (province) params.set("province", province);
    const res = await fetch(`${API}/filters?${params}`);
    const data = await res.json();
    setFilters(data);
    return data;
  }, []);

  // Load aggregations
  const loadAggregations = useCallback(async ({ level = "municipal", region, province } = {}) => {
    const params = new URLSearchParams({ level });
    if (region) params.set("region", region);
    if (province) params.set("province", province);
    const res = await fetch(`${API}/aggregations?${params}`);
    const data = await res.json();
    setAggregations(data.data);
    return data;
  }, []);

  // Load boundaries GeoJSON
  const loadBoundaries = useCallback(async (level = "municipal") => {
    const res = await fetch(`${API}/boundaries/${level}`);
    const data = await res.json();
    setBoundaries(data);
    return data;
  }, []);

  // Select a school and load its neighbors
  const selectSchool = useCallback(async (schoolId, maxKm = 20) => {
    if (!schoolId) {
      setSelectedSchool(null);
      setNeighbors(null);
      return;
    }

    const [schoolRes, neighborsRes] = await Promise.all([
      fetch(`${API}/schools/${schoolId}`),
      fetch(`${API}/schools/${schoolId}/neighbors?max_km=${maxKm}`),
    ]);
    const school = await schoolRes.json();
    const nbrs = await neighborsRes.json();

    setSelectedSchool(school);
    setNeighbors(nbrs);
    return { school, neighbors: nbrs };
  }, []);

  return {
    stats,
    filters,
    schools,
    totalSchools,
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
  };
}
