export default function StatsBar({ stats }) {
  if (!stats) return null;

  return (
    <div className="flex items-center gap-3 text-xs text-gray-500 ml-auto">
      <span>
        <b className="text-gray-700">{stats.total_schools?.toLocaleString()}</b> schools
      </span>
      <span>
        <b className="text-blue-600">{stats.public_schools?.toLocaleString()}</b> public
      </span>
      <span>
        <b className="text-orange-600">{stats.private_schools?.toLocaleString()}</b> private
      </span>
      <span>
        <b className="text-red-600">{stats.jhs_deserts?.toLocaleString()}</b> JHS deserts
      </span>
    </div>
  );
}
