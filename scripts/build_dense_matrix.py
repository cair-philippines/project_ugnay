#!/usr/bin/env python3
"""
Build a dense school-to-school distance matrix for a subset of regions.

Produces the same .npy + index JSON format used by project_paaral,
but with fresh coordinates from project_coordinates and OSRM routing.

Usage:
    # Region III + NCR + Region IV-A (project_paaral scope)
    python build_dense_matrix.py --regions "NCR" "Region III" "Region IV-A"

    # Custom output path
    python build_dense_matrix.py --regions "NCR" --output /path/to/output/

    # List available regions
    python build_dense_matrix.py --list

    # Dry run (show school count, estimated size, no computation)
    python build_dense_matrix.py --regions "NCR" "Region III" "Region IV-A" --dry-run
"""

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import requests
from tqdm import tqdm

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from modules.coordinates import load_unified, list_regions, get_region_schools
from modules.inter_island import tag_island_group
from modules.osrm_client import query_distance_matrix, MAX_COORDS_PER_REQUEST

DEFAULT_OSRM_URL = "http://osrm:5000/table/v1/driving/"
MAX_SOURCES_PER_BATCH = 500


def check_osrm(osrm_url):
    """Verify OSRM is reachable."""
    base = osrm_url.split("/table/")[0]
    try:
        resp = requests.get(f"{base}/route/v1/driving/121.0,14.5;121.1,14.6", timeout=10)
        data = resp.json()
        if data.get("code") == "Ok":
            print(f"OSRM is running.")
            return True
    except requests.ConnectionError:
        pass
    print(f"ERROR: Cannot connect to OSRM at {base}")
    return False


def build_dense_matrix(coords, school_ids, osrm_url, desc="Computing"):
    """
    Compute a full dense N×N distance matrix via OSRM.

    For large coordinate sets (>MAX_COORDS_PER_REQUEST), sub-batches both
    sources and destinations, then stitches the results into the full matrix.

    Returns
    -------
    np.ndarray
        N×N float32 array, distances in meters. np.inf where no route.
    """
    n = len(coords)
    matrix = np.full((n, n), np.inf, dtype=np.float32)
    np.fill_diagonal(matrix, 0.0)

    if n <= MAX_COORDS_PER_REQUEST:
        # All coords fit in one URL — batch sources only
        for i in tqdm(range(0, n, MAX_SOURCES_PER_BATCH), desc=desc):
            batch_end = min(i + MAX_SOURCES_PER_BATCH, n)
            src_indices = list(range(i, batch_end))

            distances = query_distance_matrix(
                coords, source_indices=src_indices, osrm_url=osrm_url
            )
            if distances is None:
                print(f"  Warning: batch [{i}..{batch_end}) failed")
                continue

            for j, src_idx in enumerate(src_indices):
                row = distances[j].astype(np.float32)
                row[~np.isfinite(row)] = np.inf
                matrix[src_idx, :] = row
    else:
        # Sub-batch both sources and destinations
        dest_batch_size = MAX_COORDS_PER_REQUEST - MAX_SOURCES_PER_BATCH

        src_batches = []
        for i in range(0, n, MAX_SOURCES_PER_BATCH):
            src_batches.append(list(range(i, min(i + MAX_SOURCES_PER_BATCH, n))))

        dest_batches = []
        for j in range(0, n, dest_batch_size):
            dest_batches.append(list(range(j, min(j + dest_batch_size, n))))

        total = len(src_batches) * len(dest_batches)
        pbar = tqdm(total=total, desc=desc)

        for src_indices in src_batches:
            for dest_indices in dest_batches:
                all_indices = sorted(set(src_indices) | set(dest_indices))
                sub_coords = [coords[k] for k in all_indices]
                idx_map = {orig: new for new, orig in enumerate(all_indices)}
                mapped_src = [idx_map[k] for k in src_indices]
                mapped_dest = [idx_map[k] for k in dest_indices]

                distances = query_distance_matrix(
                    sub_coords,
                    source_indices=mapped_src,
                    destination_indices=mapped_dest,
                    osrm_url=osrm_url,
                )

                if distances is None:
                    print(f"  Warning: sub-batch failed (src[{src_indices[0]}..], dest[{dest_indices[0]}..])")
                    pbar.update(1)
                    continue

                for i, src_orig in enumerate(src_indices):
                    for j, dest_orig in enumerate(dest_indices):
                        val = distances[i][j]
                        if np.isfinite(val):
                            matrix[src_orig, dest_orig] = np.float32(val)

                pbar.update(1)

        pbar.close()

    return matrix


def main():
    parser = argparse.ArgumentParser(
        description="Build dense distance matrix for a subset of regions",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  project_paaral scope:  python build_dense_matrix.py --regions "NCR" "Region III" "Region IV-A"
  Single region:         python build_dense_matrix.py --regions "NCR"
  Dry run:               python build_dense_matrix.py --regions "NCR" "Region III" --dry-run
        """,
    )
    parser.add_argument(
        "--regions", nargs="+", help="Regions to include in the matrix"
    )
    parser.add_argument(
        "--output", type=Path, default=PROJECT_DIR / "output" / "dense_matrix",
        help="Output directory"
    )
    parser.add_argument(
        "--osrm-url", default=DEFAULT_OSRM_URL, help="OSRM endpoint URL"
    )
    parser.add_argument(
        "--list", action="store_true", help="List available regions and exit"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show estimated size and school count without computing"
    )
    parser.add_argument(
        "--prefix", default="school_distance_matrix",
        help="Output filename prefix (default: school_distance_matrix)"
    )

    args = parser.parse_args()

    print("Loading school coordinates...")
    df_all = load_unified()
    df_all = tag_island_group(df_all)
    regions = list_regions(df_all)

    if args.list:
        print(f"\nAvailable regions ({len(regions)}):")
        for r in regions:
            n = len(get_region_schools(df_all, r))
            print(f"  {r}: {n:,} schools")
        return

    if not args.regions:
        parser.print_help()
        return

    # Filter to requested regions
    missing = [r for r in args.regions if r not in regions]
    if missing:
        print(f"ERROR: Regions not found: {missing}")
        print(f"Available: {regions}")
        sys.exit(1)

    df_subset = df_all[df_all["region"].isin(args.regions)].copy()
    df_subset = df_subset.drop_duplicates(subset="school_id", keep="first")
    df_subset = df_subset.sort_values("school_id").reset_index(drop=True)

    n = len(df_subset)
    n_pairs = n * n
    size_mb = n_pairs * 4 / 1e6  # float32 = 4 bytes

    print(f"\nRegions: {args.regions}")
    print(f"Schools: {n:,}")
    print(f"Matrix: {n:,} x {n:,} = {n_pairs:,} pairs")
    print(f"Estimated size: {size_mb:.0f} MB (.npy float32)")

    if args.dry_run:
        print("\nDry run — no computation performed.")
        return

    # Verify OSRM
    if not check_osrm(args.osrm_url):
        sys.exit(1)

    # Build matrix
    coords = list(zip(df_subset["longitude"], df_subset["latitude"]))
    school_ids = df_subset["school_id"].tolist()

    print(f"\nComputing dense matrix...")
    t0 = time.time()
    matrix = build_dense_matrix(coords, school_ids, args.osrm_url)
    elapsed = time.time() - t0
    print(f"Done in {elapsed / 60:.1f} minutes")

    # Stats
    valid = np.isfinite(matrix) & (matrix > 0)
    print(f"\nValid distances: {valid.sum():,} / {n_pairs:,} ({valid.sum()/n_pairs*100:.1f}%)")
    print(f"No route (inf): {(matrix == np.inf).sum() - n:,}")  # subtract diagonal
    if valid.any():
        print(f"Distance range: {matrix[valid].min()/1000:.1f} – {matrix[valid].max()/1000:.1f} km")

    # Save
    args.output.mkdir(parents=True, exist_ok=True)

    npy_path = args.output / f"{args.prefix}.npy"
    np.save(npy_path, matrix)
    print(f"\nSaved: {npy_path.name} ({npy_path.stat().st_size / 1e6:.0f} MB)")

    # Index file (same format as project_paaral)
    school_id_to_indices = {sid: i for i, sid in enumerate(school_ids)}
    index_data = {
        "school_ids": school_ids,
        "school_id_to_indices": school_id_to_indices,
        "regions": args.regions,
        "n_schools": n,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    index_path = args.output / f"{args.prefix}_index.json"
    with open(index_path, "w") as f:
        json.dump(index_data, f, indent=2)
    print(f"Saved: {index_path.name}")

    print(f"\n{'='*60}")
    print(f"Dense matrix ready at: {args.output}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
