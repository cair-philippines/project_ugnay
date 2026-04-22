"""
Batched OSRM Table API client for sparse edge computation.

When a batch fails, retries with progressively smaller sub-batches to
isolate problematic coordinates. Tracks which school_ids were successfully
computed vs failed, so downstream can distinguish genuine isolation from
missing data.
"""

import numpy as np
import pandas as pd
import requests
from tqdm import tqdm


# OSRM service URL (within Docker network)
DEFAULT_OSRM_URL = "http://osrm:5000/table/v1/driving/"

# Maximum coordinates per OSRM request (sources + destinations combined).
MAX_COORDS_PER_REQUEST = 2000

# Maximum sources per batch
MAX_SOURCES_PER_BATCH = 500

# Retry batch sizes when a batch fails (progressively smaller)
RETRY_SIZES = [100, 20, 1]


def _build_coord_str(coords):
    """Build semicolon-delimited coordinate string for OSRM API."""
    return ";".join(f"{lon},{lat}" for lon, lat in coords)


def query_distance_matrix(
    coords,
    source_indices=None,
    destination_indices=None,
    osrm_url=DEFAULT_OSRM_URL,
):
    """
    Query OSRM Table API for a distance matrix.

    Returns
    -------
    np.ndarray or None
        Distance matrix (n_sources x n_destinations) in meters.
        Returns None if the request fails.
    """
    coord_str = _build_coord_str(coords)
    params = {"annotations": "distance"}
    if source_indices is not None:
        params["sources"] = ";".join(map(str, source_indices))
    if destination_indices is not None:
        params["destinations"] = ";".join(map(str, destination_indices))

    try:
        response = requests.get(
            f"{osrm_url}{coord_str}",
            params=params,
            timeout=300,
        )
    except requests.RequestException as e:
        return None

    if response.status_code != 200:
        return None

    data = response.json()
    if data.get("code") != "Ok":
        return None

    distances = np.array(data["distances"], dtype=np.float64)
    distances[~np.isfinite(distances)] = np.inf
    return distances


def _retry_failed_sources(
    failed_src_indices, coords, dest_indices, school_ids,
    road_cutoff_m, osrm_url, edges, succeeded_ids, failed_ids,
    is_subbatched,
):
    """
    Retry failed source indices with progressively smaller batch sizes.

    For sub-batched regions (large), each retry builds a sub-coord set.
    For small regions, all coords are sent with source/dest params.
    """
    remaining = list(failed_src_indices)

    for retry_size in RETRY_SIZES:
        if not remaining:
            break

        still_failing = []
        for i in range(0, len(remaining), retry_size):
            sub_src = remaining[i : i + retry_size]

            if is_subbatched:
                # Build sub-coord set (src + dest)
                all_idx = sorted(set(sub_src) | set(dest_indices))
                sub_coords = [coords[j] for j in all_idx]
                idx_map = {orig: new for new, orig in enumerate(all_idx)}
                mapped_src = [idx_map[j] for j in sub_src]
                mapped_dest = [idx_map[j] for j in dest_indices]

                distances = query_distance_matrix(
                    sub_coords,
                    source_indices=mapped_src,
                    destination_indices=mapped_dest,
                    osrm_url=osrm_url,
                )

                if distances is None:
                    still_failing.extend(sub_src)
                    continue

                for row_i, src_orig in enumerate(sub_src):
                    succeeded_ids.add(school_ids[src_orig])
                    row = distances[row_i]
                    within = np.where((row <= road_cutoff_m) & (row > 0))[0]
                    for j in within:
                        dest_orig = dest_indices[j]
                        if dest_orig != src_orig:
                            edges.append((
                                school_ids[src_orig],
                                school_ids[dest_orig],
                                float(row[j]),
                            ))
            else:
                # Small region: all coords in URL, just vary sources
                distances = query_distance_matrix(
                    coords, source_indices=sub_src, osrm_url=osrm_url
                )

                if distances is None:
                    still_failing.extend(sub_src)
                    continue

                n_dest = len(coords)
                for row_i, src_idx in enumerate(sub_src):
                    succeeded_ids.add(school_ids[src_idx])
                    row = distances[row_i]
                    within = np.where((row <= road_cutoff_m) & (row > 0))[0]
                    for j in within:
                        if j != src_idx:
                            edges.append((
                                school_ids[src_idx],
                                school_ids[j],
                                float(row[j]),
                            ))

        remaining = still_failing

        if remaining:
            retry_desc = f"batch={retry_size}"
            if retry_size > 1:
                next_size = RETRY_SIZES[RETRY_SIZES.index(retry_size) + 1] if retry_size != RETRY_SIZES[-1] else None
                if next_size:
                    pass  # will retry at next size

    # Whatever still remains after all retries is truly failed
    for idx in remaining:
        failed_ids.add(school_ids[idx])


def compute_sparse_edges(
    coords,
    school_ids,
    road_cutoff_m=20_000,
    osrm_url=DEFAULT_OSRM_URL,
    desc="Computing edges",
):
    """
    Compute sparse edges between schools using OSRM Table API.

    When a batch fails, retries with smaller sub-batches (100 → 20 → 1)
    to isolate problematic coordinates.

    Returns
    -------
    tuple of (pd.DataFrame, set, set)
        - Edge DataFrame with columns: source_id, target_id, road_distance_m
        - succeeded_ids: school_ids that were successfully computed
        - failed_ids: school_ids where OSRM failed even at batch size 1
    """
    n = len(coords)
    if n == 0:
        empty = pd.DataFrame(columns=["source_id", "target_id", "road_distance_m"])
        return empty, set(), set()

    if n != len(school_ids):
        raise ValueError(
            f"coords ({n}) and school_ids ({len(school_ids)}) must have same length"
        )

    if n <= MAX_COORDS_PER_REQUEST:
        return _compute_small_region(
            coords, school_ids, road_cutoff_m, osrm_url, desc
        )
    else:
        return _compute_large_region(
            coords, school_ids, road_cutoff_m, osrm_url, desc
        )


def _compute_small_region(coords, school_ids, road_cutoff_m, osrm_url, desc):
    """Small region: all coords fit in one URL. Batch sources only."""
    n = len(coords)
    edges = []
    succeeded_ids = set()
    failed_ids = set()

    for i in tqdm(range(0, n, MAX_SOURCES_PER_BATCH), desc=desc):
        batch_end = min(i + MAX_SOURCES_PER_BATCH, n)
        batch_indices = list(range(i, batch_end))

        distances = query_distance_matrix(
            coords, source_indices=batch_indices, osrm_url=osrm_url
        )

        if distances is None:
            print(f"  Batch [{i}..{batch_end}) failed, retrying with smaller batches...")
            _retry_failed_sources(
                batch_indices, coords, list(range(n)), school_ids,
                road_cutoff_m, osrm_url, edges, succeeded_ids, failed_ids,
                is_subbatched=False,
            )
            continue

        # Success — record all source IDs and extract edges
        for j, src_idx in enumerate(batch_indices):
            succeeded_ids.add(school_ids[src_idx])
            row = distances[j]
            within = np.where((row <= road_cutoff_m) & (row > 0))[0]
            for k in within:
                if k != src_idx:
                    edges.append((
                        school_ids[src_idx],
                        school_ids[k],
                        float(row[k]),
                    ))

    if failed_ids:
        print(f"  {len(failed_ids)} schools failed all OSRM retries")

    df = pd.DataFrame(edges, columns=["source_id", "target_id", "road_distance_m"])
    if len(df) > 0:
        df["road_distance_m"] = df["road_distance_m"].astype(np.float32)
    return df, succeeded_ids, failed_ids


def _compute_large_region(coords, school_ids, road_cutoff_m, osrm_url, desc):
    """Large region: sub-batch both sources and destinations."""
    n = len(coords)
    edges = []
    succeeded_ids = set()
    failed_ids = set()

    dest_batch_size = MAX_COORDS_PER_REQUEST - MAX_SOURCES_PER_BATCH

    src_batches = []
    for i in range(0, n, MAX_SOURCES_PER_BATCH):
        src_batches.append(list(range(i, min(i + MAX_SOURCES_PER_BATCH, n))))

    dest_batches = []
    for j in range(0, n, dest_batch_size):
        dest_batches.append(list(range(j, min(j + dest_batch_size, n))))

    total_requests = len(src_batches) * len(dest_batches)
    pbar = tqdm(total=total_requests, desc=desc)

    for src_indices in src_batches:
        for dest_indices in dest_batches:
            all_indices = sorted(set(src_indices) | set(dest_indices))
            sub_coords = [coords[i] for i in all_indices]

            idx_map = {orig: new for new, orig in enumerate(all_indices)}
            mapped_src = [idx_map[i] for i in src_indices]
            mapped_dest = [idx_map[i] for i in dest_indices]

            distances = query_distance_matrix(
                sub_coords,
                source_indices=mapped_src,
                destination_indices=mapped_dest,
                osrm_url=osrm_url,
            )

            if distances is None:
                # Retry this src batch against this dest batch
                _retry_failed_sources(
                    src_indices, coords, dest_indices, school_ids,
                    road_cutoff_m, osrm_url, edges, succeeded_ids, failed_ids,
                    is_subbatched=True,
                )
                pbar.update(1)
                continue

            for i, src_orig in enumerate(src_indices):
                succeeded_ids.add(school_ids[src_orig])
                row = distances[i]
                within = np.where((row <= road_cutoff_m) & (row > 0))[0]
                for j in within:
                    dest_orig = dest_indices[j]
                    if dest_orig != src_orig:
                        edges.append((
                            school_ids[src_orig],
                            school_ids[dest_orig],
                            float(row[j]),
                        ))

            pbar.update(1)

    pbar.close()

    if failed_ids:
        print(f"  {len(failed_ids)} schools failed all OSRM retries")

    df = pd.DataFrame(edges, columns=["source_id", "target_id", "road_distance_m"])
    if len(df) > 0:
        df["road_distance_m"] = df["road_distance_m"].astype(np.float32)
        df = df.drop_duplicates(subset=["source_id", "target_id"], keep="first")
    return df, succeeded_ids, failed_ids
