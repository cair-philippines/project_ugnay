"""
Distance lookup utility for school-to-school road distances.

Loads the sparse edge table from project_ugnay and provides fast
origin-destination distance queries. Designed to be imported by
other projects (project_paaral, paaral_eda, etc.) as a drop-in
replacement for the old dense .npy distance matrix.

Usage as a module:
    from distance_lookup import DistanceLookup

    dist = DistanceLookup("/path/to/all_edges.parquet")
    km = dist.get("136718", "320102")           # single pair
    km = dist.get_meters("136718", "320102")    # in meters
    df = dist.get_neighbors("136718", max_km=5) # all within 5 km
    results = dist.get_many(pairs)              # batch lookup

Usage as CLI:
    python distance_lookup.py /path/to/all_edges.parquet 136718 320102
    python distance_lookup.py /path/to/all_edges.parquet 136718 --neighbors 5
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd


class DistanceLookup:
    """
    Fast school-to-school road distance lookup from sparse edge table.

    Parameters
    ----------
    edges_path : str or Path
        Path to all_edges.parquet (or any parquet with source_id,
        target_id, road_distance_m columns).
    """

    def __init__(self, edges_path):
        edges_path = Path(edges_path)
        if not edges_path.exists():
            raise FileNotFoundError(f"Edge table not found: {edges_path}")

        self._df = pd.read_parquet(
            edges_path,
            columns=["source_id", "target_id", "road_distance_m"],
        )
        self._df["source_id"] = self._df["source_id"].astype(str)
        self._df["target_id"] = self._df["target_id"].astype(str)

        # Build a dict-of-dicts for O(1) lookup
        self._index = {}
        for src, tgt, dist in zip(
            self._df["source_id"], self._df["target_id"], self._df["road_distance_m"]
        ):
            if src not in self._index:
                self._index[src] = {}
            self._index[src][tgt] = float(dist)

        self._n_edges = len(self._df)
        self._n_schools = len(self._index)

    def __repr__(self):
        return f"DistanceLookup({self._n_schools:,} schools, {self._n_edges:,} edges)"

    def get_meters(self, origin_id, dest_id):
        """
        Get road distance in meters between two schools.

        Returns np.inf if no edge exists (schools are >20 km apart,
        on different islands, or one/both are not in the edge table).
        Returns 0.0 if origin == destination.
        """
        origin_id, dest_id = str(origin_id), str(dest_id)
        if origin_id == dest_id:
            return 0.0
        return self._index.get(origin_id, {}).get(dest_id, np.inf)

    def get(self, origin_id, dest_id):
        """Get road distance in kilometers."""
        m = self.get_meters(origin_id, dest_id)
        return m / 1000 if np.isfinite(m) else np.inf

    def get_many(self, pairs):
        """
        Batch distance lookup.

        Parameters
        ----------
        pairs : list of (origin_id, dest_id) tuples

        Returns
        -------
        list of float
            Distances in kilometers. np.inf for missing pairs.
        """
        return [self.get(o, d) for o, d in pairs]

    def get_neighbors(self, school_id, max_km=20):
        """
        Get all neighbors of a school within max_km.

        Parameters
        ----------
        school_id : str
            Origin school ID.
        max_km : float
            Maximum distance in km.

        Returns
        -------
        pd.DataFrame
            Neighbors sorted by distance, with columns:
            target_id, road_distance_km.
        """
        school_id = str(school_id)
        targets = self._index.get(school_id, {})
        if not targets:
            return pd.DataFrame(columns=["target_id", "road_distance_km"])

        max_m = max_km * 1000
        rows = [
            (tid, dist / 1000)
            for tid, dist in targets.items()
            if dist <= max_m
        ]
        df = pd.DataFrame(rows, columns=["target_id", "road_distance_km"])
        return df.sort_values("road_distance_km").reset_index(drop=True)

    def has_school(self, school_id):
        """Check if a school exists in the edge table (as a source)."""
        return str(school_id) in self._index

    def school_ids(self):
        """Return set of all school IDs that appear as sources."""
        return set(self._index.keys())

    @property
    def n_schools(self):
        return self._n_schools

    @property
    def n_edges(self):
        return self._n_edges


# --- Default instance loader ---

_DEFAULT_EDGES_PATH = (
    Path(__file__).resolve().parent.parent / "output" / "edges" / "all_edges.parquet"
)


def load(edges_path=None):
    """
    Load a DistanceLookup instance.

    Parameters
    ----------
    edges_path : str or Path, optional
        Path to all_edges.parquet. Defaults to project_ugnay's local output.

    Returns
    -------
    DistanceLookup
    """
    if edges_path is None:
        edges_path = _DEFAULT_EDGES_PATH
    return DistanceLookup(edges_path)


# --- CLI ---

def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Look up road distances between Philippine schools"
    )
    parser.add_argument("edges_path", help="Path to all_edges.parquet")
    parser.add_argument("origin", help="Origin school ID")
    parser.add_argument("dest", nargs="?", help="Destination school ID")
    parser.add_argument(
        "--neighbors", type=float, metavar="KM",
        help="List all neighbors within KM kilometers (instead of single pair)"
    )

    args = parser.parse_args()
    dist = DistanceLookup(args.edges_path)
    print(dist)

    if args.neighbors is not None:
        df = dist.get_neighbors(args.origin, max_km=args.neighbors)
        if len(df) == 0:
            print(f"No neighbors within {args.neighbors} km")
        else:
            print(f"\n{len(df)} neighbors within {args.neighbors} km:")
            print(df.to_string(index=False))
    elif args.dest:
        km = dist.get(args.origin, args.dest)
        if np.isinf(km):
            print(f"No route (>20 km or different islands)")
        else:
            print(f"{km:.2f} km")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
