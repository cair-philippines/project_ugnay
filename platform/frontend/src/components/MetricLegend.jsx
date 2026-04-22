const METRIC_LABELS = {
  mean_isolation_score: "Isolation Score",
  mean_feeder_isolation: "Feeder Isolation",
  pct_private_desert: "Private Desert %",
  pct_esc_desert: "ESC Desert %",
  pct_jhs_desert: "JHS Desert %",
  pct_shs_desert: "SHS Desert %",
  mean_nearest_private_km: "Avg Nearest Private (km)",
  mean_nearest_esc_km: "Avg Nearest ESC (km)",
};

export default function MetricLegend({ metric }) {
  const label = METRIC_LABELS[metric] || metric;

  return (
    <div className="absolute bottom-4 left-4 bg-white rounded shadow-md px-3 py-2 z-10">
      <p className="text-xs font-medium text-gray-700 mb-1">{label}</p>
      <div className="flex items-center gap-1">
        <span className="text-[10px] text-gray-500">Low</span>
        <div
          className="h-3 w-32 rounded"
          style={{
            background:
              "linear-gradient(to right, #00cc00, #cccc00, #cc0000)",
          }}
        />
        <span className="text-[10px] text-gray-500">High</span>
      </div>
      <div className="flex items-center gap-2 mt-1">
        <span className="inline-block w-3 h-3 rounded-full bg-blue-500" />
        <span className="text-[10px] text-gray-600">Public</span>
        <span className="inline-block w-3 h-3 rounded-full bg-orange-500" />
        <span className="text-[10px] text-gray-600">Private</span>
      </div>
    </div>
  );
}
