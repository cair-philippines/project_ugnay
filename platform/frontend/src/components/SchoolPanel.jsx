import { X } from "lucide-react";

function MetricRow({ label, value, unit = "" }) {
  if (value == null || value === Infinity || (typeof value === "number" && isNaN(value))) {
    return (
      <div className="flex justify-between text-sm py-0.5">
        <span className="text-gray-500">{label}</span>
        <span className="text-gray-400">N/A</span>
      </div>
    );
  }
  const display = typeof value === "number" ? value.toFixed(1) : String(value);
  return (
    <div className="flex justify-between text-sm py-0.5">
      <span className="text-gray-500">{label}</span>
      <span className="font-medium">
        {display}
        {unit}
      </span>
    </div>
  );
}

function BoolBadge({ label, value, goodWhenFalse = true }) {
  const isGood = goodWhenFalse ? !value : value;
  return (
    <span
      className={`inline-block px-2 py-0.5 rounded text-xs font-medium mr-1 mb-1 ${
        isGood
          ? "bg-green-100 text-green-800"
          : "bg-red-100 text-red-800"
      }`}
    >
      {label}: {value ? "Yes" : "No"}
    </span>
  );
}

export default function SchoolPanel({ school, neighbors, onClose }) {
  if (!school) return null;

  const s = school;
  const nbrs = neighbors?.neighbors || [];

  return (
    <div className="w-96 bg-white border-l border-gray-200 overflow-y-auto p-4">
      {/* Header */}
      <div className="flex justify-between items-start mb-3">
        <div>
          <h2 className="font-semibold text-gray-900 text-sm leading-tight">
            {s.school_name || s.school_id}
          </h2>
          <p className="text-xs text-gray-500 mt-0.5">
            {s.school_id} | {s.sector}
          </p>
          <p className="text-xs text-gray-500">
            {s.municipality}, {s.province}, {s.region}
          </p>
        </div>
        <button
          onClick={onClose}
          className="p-1 hover:bg-gray-100 rounded"
        >
          <X size={16} />
        </button>
      </div>

      {/* Offerings */}
      <div className="mb-3">
        <p className="text-xs font-medium text-gray-700 mb-1">Offerings</p>
        <div className="flex gap-1">
          {s.offers_es === "True" || s.offers_es === true ? (
            <span className="px-2 py-0.5 bg-blue-100 text-blue-800 rounded text-xs">ES</span>
          ) : null}
          {s.offers_jhs === "True" || s.offers_jhs === true ? (
            <span className="px-2 py-0.5 bg-indigo-100 text-indigo-800 rounded text-xs">JHS</span>
          ) : null}
          {s.offers_shs === "True" || s.offers_shs === true ? (
            <span className="px-2 py-0.5 bg-purple-100 text-purple-800 rounded text-xs">SHS</span>
          ) : null}
        </div>
      </div>

      {/* General metrics */}
      <div className="mb-3 border-t pt-2">
        <p className="text-xs font-medium text-gray-700 mb-1">
          General Connectivity
        </p>
        <MetricRow label="Isolation score" value={s.isolation_score} />
        <MetricRow label="Neighbors (5 km)" value={s.n_neighbors_5km} />
        <MetricRow label="Neighbors (10 km)" value={s.n_neighbors_10km} />
        <MetricRow label="Neighbors (20 km)" value={s.n_neighbors_20km} />
        <MetricRow label="Nearest private" value={s.nearest_private_km} unit=" km" />
        <MetricRow label="Nearest ESC" value={s.nearest_esc_km} unit=" km" />
        <MetricRow label="Road detour" value={s.mean_road_haversine_ratio} unit="x" />
      </div>

      {/* Desert flags */}
      <div className="mb-3">
        <BoolBadge label="Private desert" value={s.private_desert} />
        <BoolBadge label="ESC desert" value={s.esc_desert} />
        <BoolBadge label="JHS desert" value={s.jhs_desert} />
        <BoolBadge label="SHS desert" value={s.shs_desert} />
      </div>

      {/* Feeder metrics */}
      <div className="mb-3 border-t pt-2">
        <p className="text-xs font-medium text-gray-700 mb-1">
          Feeder Connectivity
        </p>
        <MetricRow label="Feeder isolation" value={s.feeder_isolation_score} />
        {(s.offers_es === "True" || s.offers_es === true) && (
          <>
            <MetricRow label="Nearest JHS" value={s.nearest_jhs_km} unit=" km" />
            <MetricRow label="JHS within 10 km" value={s.n_jhs_10km} />
            <BoolBadge label="Self-feeds JHS" value={s.self_feeds_jhs} goodWhenFalse={false} />
          </>
        )}
        {(s.offers_jhs === "True" || s.offers_jhs === true) && (
          <>
            <MetricRow label="Nearest SHS" value={s.nearest_shs_km} unit=" km" />
            <MetricRow label="SHS within 10 km" value={s.n_shs_10km} />
            <BoolBadge label="Self-feeds SHS" value={s.self_feeds_shs} goodWhenFalse={false} />
          </>
        )}
      </div>

      {/* Neighbors */}
      <div className="border-t pt-2">
        <p className="text-xs font-medium text-gray-700 mb-1">
          Neighbors ({nbrs.length})
        </p>
        <div className="max-h-64 overflow-y-auto">
          {nbrs.slice(0, 50).map((n) => (
            <div
              key={n.target_id}
              className="flex justify-between text-xs py-1 border-b border-gray-50"
            >
              <div className="flex-1 truncate pr-2">
                <span className="text-gray-800">
                  {n.target_name || n.target_id}
                </span>
                <span className="text-gray-400 ml-1">
                  {n.target_sector}
                </span>
              </div>
              <span className="text-gray-600 whitespace-nowrap">
                {(n.road_distance_m / 1000).toFixed(1)} km
              </span>
            </div>
          ))}
          {nbrs.length > 50 && (
            <p className="text-xs text-gray-400 mt-1">
              +{nbrs.length - 50} more
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
