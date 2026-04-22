"""
Regional sparse edge computation with haversine pre-filtering.

Orchestrates the OSRM client to compute school-to-school road distances
region by region, using haversine distance as a pre-filter to avoid
sending all 57K schools to OSRM at once.
"""

from pathlib import Path

import numpy as np
import pandas as pd
from scipy.spatial import cKDTree

from .coordinates import get_region_schools, list_regions
from .inter_island import tag_sea_separated
from .osrm_client import compute_sparse_edges


# Defaults
DEFAULT_HAVERSINE_CUTOFF_KM = 30.0
DEFAULT_ROAD_CUTOFF_M = 20_000
EARTH_RADIUS_KM = 6371.0


def _haversine_km(lat1, lon1, lat2, lon2):
    """Vectorized haversine distance in kilometers."""
    lat1, lon1, lat2, lon2 = map(np.radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = np.sin(dlat / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2) ** 2
    return EARTH_RADIUS_KM * 2 * np.arcsin(np.sqrt(a))


def _find_candidates_kdtree(df, haversine_cutoff_km):
    """
    Use a KDTree to find candidate school pairs within haversine cutoff.

    Returns a set of (source_idx, target_idx) pairs where both are
    positional indices into df.
    """
    # Convert lat/lon to radians for KDTree on a unit sphere
    lats = np.radians(df["latitude"].values)
    lons = np.radians(df["longitude"].values)
    x = np.cos(lats) * np.cos(lons)
    y = np.cos(lats) * np.sin(lons)
    z = np.sin(lats)
    points = np.column_stack([x, y, z])

    # Convert haversine cutoff to Euclidean chord distance on unit sphere
    chord = 2 * np.sin(np.radians(haversine_cutoff_km / EARTH_RADIUS_KM * 180 / np.pi / 2))
    # Simpler: chord = 2 * sin(cutoff_km / (2 * R))
    chord = 2 * np.sin(haversine_cutoff_km / (2 * EARTH_RADIUS_KM))

    tree = cKDTree(points)
    pairs = tree.query_pairs(r=chord)
    return pairs


def build_region_edges(
    df_region,
    haversine_cutoff_km=DEFAULT_HAVERSINE_CUTOFF_KM,
    road_cutoff_m=DEFAULT_ROAD_CUTOFF_M,
    osrm_url="http://osrm:5000/table/v1/driving/",
    region_name="",
):
    """
    Compute sparse edges for all schools within a single region.

    Strategy:
    1. Use KDTree to find all school pairs within haversine_cutoff_km
    2. Collect the unique set of schools involved in any candidate pair
    3. Send these to OSRM in batches
    4. Filter to road_cutoff_m

    Parameters
    ----------
    df_region : pd.DataFrame
        Schools in this region (must have school_id, latitude, longitude).
    haversine_cutoff_km : float
        Pre-filter cutoff in km (should be > road_cutoff to account for
        road-to-haversine ratio).
    road_cutoff_m : float
        Final road distance cutoff in meters.
    osrm_url : str
        OSRM Table API endpoint.
    region_name : str
        Region name for logging.

    Returns
    -------
    tuple of (pd.DataFrame, set, set)
        - Edge DataFrame with columns: source_id, target_id,
          road_distance_m, haversine_distance_m, road_haversine_ratio.
        - succeeded_ids: school_ids successfully computed by OSRM.
        - failed_ids: school_ids where OSRM failed even after retries.
    """
    n = len(df_region)
    if n == 0:
        return _empty_edges(), set(), set()

    desc = f"Region: {region_name}" if region_name else "Computing edges"
    print(f"\n{desc}: {n:,} schools")

    coords = list(zip(df_region["longitude"], df_region["latitude"]))
    school_ids = df_region["school_id"].tolist()

    # For small regions, skip haversine pre-filter and send all to OSRM
    if n <= 500:
        print(f"  Small region, sending all {n} schools to OSRM")
        df_edges, succeeded_ids, failed_ids = compute_sparse_edges(
            coords, school_ids,
            road_cutoff_m=road_cutoff_m,
            osrm_url=osrm_url,
            desc=desc,
        )
    else:
        # Use KDTree pre-filter
        pairs = _find_candidates_kdtree(df_region, haversine_cutoff_km)
        print(f"  KDTree found {len(pairs):,} candidate pairs within {haversine_cutoff_km} km haversine")

        if len(pairs) == 0:
            return _empty_edges(), set(school_ids), set()

        df_edges, succeeded_ids, failed_ids = compute_sparse_edges(
            coords, school_ids,
            road_cutoff_m=road_cutoff_m,
            osrm_url=osrm_url,
            desc=desc,
        )

    # Add haversine distances
    if len(df_edges) > 0:
        df_edges = _add_haversine(df_edges, df_region)

    print(f"  Edges within {road_cutoff_m/1000:.0f} km road: {len(df_edges):,}")
    if failed_ids:
        print(f"  OSRM failures: {len(failed_ids):,} schools")
    return df_edges, succeeded_ids, failed_ids


def build_cross_region_edges(
    df_all,
    region_a,
    region_b,
    haversine_cutoff_km=DEFAULT_HAVERSINE_CUTOFF_KM,
    road_cutoff_m=DEFAULT_ROAD_CUTOFF_M,
    osrm_url="http://osrm:5000/table/v1/driving/",
):
    """
    Compute edges between schools in two adjacent regions.

    Only considers schools within haversine_cutoff_km of the other region's
    nearest school.

    Parameters
    ----------
    df_all : pd.DataFrame
        Full unified coordinates.
    region_a, region_b : str
        Region names.

    Returns
    -------
    pd.DataFrame
        Cross-region edges with is_cross_region=True.
    """
    df_a = get_region_schools(df_all, region_a)
    df_b = get_region_schools(df_all, region_b)

    if len(df_a) == 0 or len(df_b) == 0:
        return _empty_edges()

    # Find schools in A that are within haversine_cutoff_km of any school in B
    lats_a, lons_a = df_a["latitude"].values, df_a["longitude"].values
    lats_b, lons_b = df_b["latitude"].values, df_b["longitude"].values

    # For each school in A, find min haversine distance to any school in B
    # Use KDTree for efficiency
    coords_b_rad = np.radians(np.column_stack([lats_b, lons_b]))
    x_b = np.cos(coords_b_rad[:, 0]) * np.cos(coords_b_rad[:, 1])
    y_b = np.cos(coords_b_rad[:, 0]) * np.sin(coords_b_rad[:, 1])
    z_b = np.sin(coords_b_rad[:, 0])
    tree_b = cKDTree(np.column_stack([x_b, y_b, z_b]))

    coords_a_rad = np.radians(np.column_stack([lats_a, lons_a]))
    x_a = np.cos(coords_a_rad[:, 0]) * np.cos(coords_a_rad[:, 1])
    y_a = np.cos(coords_a_rad[:, 0]) * np.sin(coords_a_rad[:, 1])
    z_a = np.sin(coords_a_rad[:, 0])

    chord = 2 * np.sin(haversine_cutoff_km / (2 * EARTH_RADIUS_KM))
    dists_a, _ = tree_b.query(np.column_stack([x_a, y_a, z_a]))
    mask_a = dists_a <= chord
    df_a_boundary = df_a.iloc[mask_a]

    # Vice versa: schools in B near A
    tree_a = cKDTree(np.column_stack([x_a, y_a, z_a]))
    dists_b, _ = tree_a.query(np.column_stack([x_b, y_b, z_b]))
    mask_b = dists_b <= chord
    df_b_boundary = df_b.iloc[mask_b]

    n_a = len(df_a_boundary)
    n_b = len(df_b_boundary)

    if n_a == 0 or n_b == 0:
        return _empty_edges()

    print(f"\nCross-region {region_a} ↔ {region_b}: {n_a} + {n_b} boundary schools")

    # Combine boundary schools and compute edges
    df_combined = pd.concat([df_a_boundary, df_b_boundary], ignore_index=True)
    coords = list(zip(df_combined["longitude"], df_combined["latitude"]))
    school_ids = df_combined["school_id"].tolist()

    # We only want cross-region edges, so compute all then filter
    df_edges, _, _ = compute_sparse_edges(
        coords, school_ids,
        road_cutoff_m=road_cutoff_m,
        osrm_url=osrm_url,
        desc=f"Cross: {region_a} ↔ {region_b}",
    )

    if len(df_edges) == 0:
        return _empty_edges()

    # Keep only edges where source and target are in different regions
    a_ids = set(df_a_boundary["school_id"].tolist())
    b_ids = set(df_b_boundary["school_id"].tolist())
    cross_mask = (
        (df_edges["source_id"].isin(a_ids) & df_edges["target_id"].isin(b_ids))
        | (df_edges["source_id"].isin(b_ids) & df_edges["target_id"].isin(a_ids))
    )
    df_edges = df_edges[cross_mask].copy()

    if len(df_edges) > 0:
        df_edges = _add_haversine(df_edges, df_combined)

    print(f"  Cross-region edges: {len(df_edges):,}")
    return df_edges


def build_all_edges(
    df_all,
    haversine_cutoff_km=DEFAULT_HAVERSINE_CUTOFF_KM,
    road_cutoff_m=DEFAULT_ROAD_CUTOFF_M,
    osrm_url="http://osrm:5000/table/v1/driving/",
    output_dir=None,
    adjacent_regions=None,
):
    """
    Compute sparse edges for all regions, plus cross-region boundary edges.

    Parameters
    ----------
    df_all : pd.DataFrame
        Full unified coordinates from coordinates.load_unified().
    haversine_cutoff_km : float
        Haversine pre-filter cutoff.
    road_cutoff_m : float
        Road distance cutoff in meters.
    osrm_url : str
        OSRM endpoint.
    output_dir : Path, optional
        Directory to save per-region parquet files. If None, files are not
        saved (edges are only returned).
    adjacent_regions : list of (str, str), optional
        Pairs of adjacent regions for cross-region computation. If None,
        a default list of Philippine region adjacencies is used.

    Returns
    -------
    pd.DataFrame
        Combined sparse edge table for all regions + cross-region.
    """
    if output_dir is not None:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

    regions = list_regions(df_all)
    all_edges = []

    # --- Within-region edges ---
    for region in regions:
        df_region = get_region_schools(df_all, region)
        df_edges = build_region_edges(
            df_region,
            haversine_cutoff_km=haversine_cutoff_km,
            road_cutoff_m=road_cutoff_m,
            osrm_url=osrm_url,
            region_name=region,
        )
        df_edges["source_region"] = region
        df_edges["is_cross_region"] = False

        if output_dir is not None and len(df_edges) > 0:
            safe_name = region.lower().replace(" ", "_").replace("-", "_")
            path = output_dir / f"region_{safe_name}.parquet"
            df_edges.to_parquet(path, index=False)
            print(f"  Saved: {path.name}")

        all_edges.append(df_edges)

    # --- Cross-region edges ---
    if adjacent_regions is None:
        adjacent_regions = _default_adjacent_regions()

    cross_edges = []
    for region_a, region_b in adjacent_regions:
        if region_a not in regions or region_b not in regions:
            continue
        df_cross = build_cross_region_edges(
            df_all, region_a, region_b,
            haversine_cutoff_km=haversine_cutoff_km,
            road_cutoff_m=road_cutoff_m,
            osrm_url=osrm_url,
        )
        if len(df_cross) > 0:
            df_cross["is_cross_region"] = True
            cross_edges.append(df_cross)

    if cross_edges:
        df_cross_all = pd.concat(cross_edges, ignore_index=True)
        if output_dir is not None:
            path = output_dir / "cross_region_pairs.parquet"
            df_cross_all.to_parquet(path, index=False)
            print(f"  Saved: {path.name}")
        all_edges.append(df_cross_all)

    # --- Combine ---
    df_combined = pd.concat(all_edges, ignore_index=True)

    # Tag sea-separated edges
    df_combined = tag_sea_separated(df_combined)

    print(f"\n{'='*60}")
    print(f"Total edges: {len(df_combined):,}")
    print(f"  Within-region: {(~df_combined['is_cross_region']).sum():,}")
    print(f"  Cross-region:  {df_combined['is_cross_region'].sum():,}")
    print(f"  Sea-separated: {df_combined.get('is_sea_separated', pd.Series(dtype=bool)).sum():,}")
    print(f"{'='*60}")

    return df_combined


def _add_haversine(df_edges, df_schools):
    """Add haversine_distance_m and road_haversine_ratio columns to edges."""
    coord_lookup = df_schools.set_index("school_id")[["latitude", "longitude"]]

    src_coords = coord_lookup.reindex(df_edges["source_id"])
    tgt_coords = coord_lookup.reindex(df_edges["target_id"])

    hav_km = _haversine_km(
        src_coords["latitude"].values, src_coords["longitude"].values,
        tgt_coords["latitude"].values, tgt_coords["longitude"].values,
    )
    df_edges["haversine_distance_m"] = (hav_km * 1000).astype(np.float32)
    df_edges["road_haversine_ratio"] = np.where(
        df_edges["haversine_distance_m"] > 0,
        df_edges["road_distance_m"] / df_edges["haversine_distance_m"],
        np.nan,
    ).astype(np.float32)

    return df_edges


def _empty_edges():
    """Return empty edge DataFrame with correct schema."""
    return pd.DataFrame({
        "source_id": pd.Series(dtype=str),
        "target_id": pd.Series(dtype=str),
        "road_distance_m": pd.Series(dtype=np.float32),
        "haversine_distance_m": pd.Series(dtype=np.float32),
        "road_haversine_ratio": pd.Series(dtype=np.float32),
    })


def _default_adjacent_regions():
    """
    Return default list of adjacent Philippine region pairs.

    These are regions that share a land border (or are connected by
    bridge/short ferry crossing that OSRM may route through).
    """
    return [
        # Luzon
        ("CAR", "Region I"),
        ("CAR", "Region II"),
        ("CAR", "Region III"),
        ("Region I", "Region II"),
        ("Region I", "Region III"),
        ("Region II", "Region III"),
        ("Region III", "NCR"),
        ("Region III", "Region I"),
        ("NCR", "Region IV-A"),
        ("NCR", "Region III"),
        ("Region IV-A", "Region III"),
        ("Region IV-A", "Region V"),
        ("Region IV-A", "MIMAROPA"),
        # Visayas
        ("Region VI", "NIR"),  # Panay ↔ Negros (NIR = Negros Island Region)
        ("Region VI", "Region VII"),  # Panay ↔ Cebu
        ("NIR", "Region VII"),  # Negros ↔ Cebu
        ("Region VII", "Region VIII"),  # Cebu/Bohol ↔ Leyte/Samar
        # Mindanao
        ("Region IX", "Region X"),
        ("Region IX", "BARMM"),
        ("Region X", "Region XI"),
        ("Region X", "Region XII"),
        ("Region X", "CARAGA"),
        ("Region XI", "Region XII"),
        ("Region XI", "CARAGA"),
        ("Region XII", "BARMM"),
        ("CARAGA", "Region X"),
    ]
