"""
Load precomputed Phase 1 + Phase 2 outputs at startup.

All data is held in memory as lists of dicts (for JSON serialization)
or DataFrames (for filtering). The platform serves ~56K schools,
~10M edges (loaded on demand per school), and aggregation summaries.
"""

import json
from pathlib import Path

import pandas as pd


def _find_data_dir():
    """Locate the data directory (works in dev and Docker)."""
    # Docker: /app/data/
    docker_path = Path("/app/data")
    if docker_path.exists():
        return docker_path

    # Dev: ../../output/ relative to backend/
    dev_path = Path(__file__).resolve().parent.parent.parent / "output"
    if dev_path.exists():
        return dev_path

    raise FileNotFoundError("Data directory not found")


def _to_records(df):
    """Convert DataFrame to list of dicts, replacing NaN with None."""
    return json.loads(df.to_json(orient="records"))


class DataStore:
    """Holds all platform data in memory."""

    def __init__(self):
        self.schools = None          # DataFrame: per-school metrics
        self.schools_records = None   # list[dict]: for JSON responses
        self.edges = None             # DataFrame: all_edges (loaded lazily)
        self.municipal = None         # list[dict]: municipal aggregation
        self.provincial = None        # list[dict]: provincial aggregation
        self.regional = None          # list[dict]: regional aggregation
        self.boundaries_municipal = None  # GeoJSON dict
        self.boundaries_provincial = None  # GeoJSON dict
        self.filter_options = None    # dict of filter dropdowns
        self.stats = None             # dict of system-wide stats

    def load(self):
        data_dir = _find_data_dir()
        print(f"Loading data from {data_dir}")

        # --- School metrics ---
        metrics_path = data_dir / "metrics" / "school_accessibility.parquet"
        self.schools = pd.read_parquet(metrics_path)
        self._normalize_schools()
        print(f"  Schools: {len(self.schools):,}")

        # --- Edges (keep as DataFrame for per-school queries) ---
        edges_path = data_dir / "edges" / "all_edges.parquet"
        self.edges = pd.read_parquet(edges_path, columns=[
            "source_id", "target_id", "road_distance_m",
            "haversine_distance_m", "road_haversine_ratio",
        ])
        print(f"  Edges: {len(self.edges):,}")

        # --- Aggregations ---
        agg_dir = data_dir / "aggregations"
        self.municipal = _to_records(pd.read_parquet(agg_dir / "municipal_summary.parquet"))
        self.provincial = _to_records(pd.read_parquet(agg_dir / "provincial_summary.parquet"))
        self.regional = _to_records(pd.read_parquet(agg_dir / "regional_summary.parquet"))
        print(f"  Aggregations: {len(self.municipal)} municipal, {len(self.provincial)} provincial, {len(self.regional)} regional")

        # --- Boundaries ---
        boundaries_dir = data_dir / "boundaries"
        if boundaries_dir.exists():
            muni_path = boundaries_dir / "municipal_boundaries.geojson"
            prov_path = boundaries_dir / "provincial_boundaries.geojson"
            if muni_path.exists():
                self.boundaries_municipal = json.loads(muni_path.read_text())
                print(f"  Municipal boundaries: {len(self.boundaries_municipal.get('features', []))} features")
            if prov_path.exists():
                self.boundaries_provincial = json.loads(prov_path.read_text())
                print(f"  Provincial boundaries: {len(self.boundaries_provincial.get('features', []))} features")

        # --- Filter options ---
        self.filter_options = self._build_filter_options()

        # --- Stats ---
        self.stats = self._build_stats()

        print("Data loaded.")

    def _normalize_schools(self):
        """Normalize school data for consistent API responses."""
        df = self.schools
        df["school_id"] = df["school_id"].astype(str)

        # Clean location strings (replace NaN with None, strip whitespace)
        for col in ["region", "province", "municipality"]:
            if col in df.columns:
                df[col] = df[col].astype(str).str.strip()
                df.loc[df[col].isin(["", "nan", "None", "none"]), col] = None

        # Build records for full list responses
        self.schools_records = _to_records(df)

        # Build school_id index for fast lookup
        self._school_index = {
            str(r["school_id"]): r for r in self.schools_records
        }

    def get_school(self, school_id):
        """Get a single school by ID."""
        return self._school_index.get(str(school_id))

    def get_neighbors(self, school_id, max_km=20):
        """Get edges from a school to its neighbors."""
        max_m = max_km * 1000
        mask = (self.edges["source_id"] == str(school_id)) & (self.edges["road_distance_m"] <= max_m)
        neighbor_edges = self.edges[mask].sort_values("road_distance_m")

        results = []
        for _, edge in neighbor_edges.iterrows():
            target = self.get_school(edge["target_id"])
            results.append({
                "target_id": edge["target_id"],
                "road_distance_m": float(edge["road_distance_m"]),
                "haversine_distance_m": float(edge["haversine_distance_m"]) if pd.notna(edge["haversine_distance_m"]) else None,
                "road_haversine_ratio": float(edge["road_haversine_ratio"]) if pd.notna(edge["road_haversine_ratio"]) else None,
                "target_name": target["school_name"] if target else None,
                "target_lat": target["latitude"] if target else None,
                "target_lon": target["longitude"] if target else None,
                "target_sector": target["sector"] if target else None,
                "target_offers_jhs": target.get("offers_jhs") if target else None,
                "target_offers_shs": target.get("offers_shs") if target else None,
            })
        return results

    def _build_filter_options(self):
        """Build cascading filter dropdown options."""
        df = self.schools
        regions = sorted(r for r in df["region"].dropna().unique().tolist() if r)
        return {"regions": regions}

    def get_filter_options(self, region=None, province=None):
        """Get cascading filter options."""
        df = self.schools
        result = {"regions": sorted(r for r in df["region"].dropna().unique().tolist() if r)}

        if region:
            filtered = df[df["region"] == region]
            result["provinces"] = sorted(
                p for p in filtered["province"].dropna().unique().tolist() if p
            )

            if province:
                filtered = filtered[filtered["province"] == province]
                result["municipalities"] = sorted(
                    m for m in filtered["municipality"].dropna().unique().tolist() if m
                )

        return result

    def _build_stats(self):
        """Build system-wide statistics."""
        df = self.schools
        return {
            "total_schools": len(df),
            "public_schools": int((df["sector"] == "public").sum()),
            "private_schools": int((df["sector"] == "private").sum()),
            "private_deserts": int(df["private_desert"].sum()) if "private_desert" in df.columns else 0,
            "esc_deserts": int(df["esc_desert"].sum()) if "esc_desert" in df.columns else 0,
            "jhs_deserts": int(df["jhs_desert"].sum()) if "jhs_desert" in df.columns else 0,
            "shs_deserts": int(df["shs_desert"].sum()) if "shs_desert" in df.columns else 0,
        }
