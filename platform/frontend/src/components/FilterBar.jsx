export default function FilterBar({
  filters,
  region,
  province,
  municipality,
  onRegionChange,
  onProvinceChange,
  onMunicipalityChange,
}) {
  return (
    <div className="flex items-center gap-2">
      <select
        className="px-2 py-1 border border-gray-300 rounded text-sm"
        value={region}
        onChange={(e) => onRegionChange(e.target.value)}
      >
        <option value="">All Regions</option>
        {(filters.regions || []).map((r) => (
          <option key={r} value={r}>
            {r}
          </option>
        ))}
      </select>

      <select
        className="px-2 py-1 border border-gray-300 rounded text-sm"
        value={province}
        onChange={(e) => onProvinceChange(e.target.value)}
        disabled={!region}
      >
        <option value="">All Provinces</option>
        {(filters.provinces || []).map((p) => (
          <option key={p} value={p}>
            {p}
          </option>
        ))}
      </select>

      <select
        className="px-2 py-1 border border-gray-300 rounded text-sm"
        value={municipality}
        onChange={(e) => onMunicipalityChange(e.target.value)}
        disabled={!province}
      >
        <option value="">All Municipalities</option>
        {(filters.municipalities || []).map((m) => (
          <option key={m} value={m}>
            {m}
          </option>
        ))}
      </select>
    </div>
  );
}
