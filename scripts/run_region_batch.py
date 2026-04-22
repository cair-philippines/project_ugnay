#!/usr/bin/env python3
"""
Primary pipeline for computing the sparse school-to-school distance network.

Usage:
    # Full pipeline: compute all regions + cross-region + combine + upload
    python run_region_batch.py --all --cross-region --finalize

    # Run specific regions only
    python run_region_batch.py "Region IV-A" "NCR"

    # Retry a failed region (recompute even if output exists)
    python run_region_batch.py "Region IV-A" --force

    # Cross-region edges only
    python run_region_batch.py --cross-region

    # Combine existing per-region files + generate manifest + upload to GCS
    python run_region_batch.py --finalize

    # List regions and their computation status
    python run_region_batch.py --list

    # Skip GCS upload during finalize (local only)
    python run_region_batch.py --finalize --no-upload
"""

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests

# Add project root to path
PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from modules.coordinates import load_unified, list_regions, get_region_schools
from modules.sparse_edges import (
    build_region_edges,
    build_cross_region_edges,
    _default_adjacent_regions,
)
from modules.inter_island import tag_island_group, tag_sea_separated
from modules.gcs_utils import (
    OUTPUT_DIR,
    UGNAY_EDGES_DIR,
    UGNAY_COORDINATES_DIR,
    upload_parquet,
    upload_json,
)

# Defaults
HAVERSINE_CUTOFF_KM = 30.0
ROAD_CUTOFF_M = 20_000
OSRM_URL = "http://osrm:5000/table/v1/driving/"
EDGES_DIR = OUTPUT_DIR / "edges"


def check_osrm(osrm_url):
    """Verify OSRM is reachable. Exit if not."""
    base = osrm_url.split("/table/")[0]
    test_url = f"{base}/route/v1/driving/121.0,14.5;121.1,14.6"
    try:
        resp = requests.get(test_url, timeout=10)
        data = resp.json()
        if data.get("code") == "Ok":
            dist_km = data["routes"][0]["distance"] / 1000
            print(f"OSRM is running. Test route: {dist_km:.1f} km")
            return True
        else:
            print(f"OSRM returned unexpected code: {data.get('code')}")
            return False
    except requests.ConnectionError:
        print(f"ERROR: Cannot connect to OSRM at {base}")
        print("Start it with: docker compose --profile routing up -d")
        return False


def run_region(df_all, region, osrm_url, force=False):
    """Compute edges for a single region. Returns (succeeded_ids, failed_ids)."""
    safe_name = region.lower().replace(" ", "_").replace("-", "_")
    out_path = EDGES_DIR / f"region_{safe_name}.parquet"
    status_path = EDGES_DIR / f"region_{safe_name}_status.json"

    if out_path.exists() and not force:
        df_existing = pd.read_parquet(out_path)
        print(f"Skipping {region} — already exists ({len(df_existing):,} edges)")
        print(f"  Use --force to recompute.")
        # Load saved status if available
        if status_path.exists():
            status = json.load(open(status_path))
            return set(status.get("succeeded", [])), set(status.get("failed", []))
        return set(), set()

    df_region = get_region_schools(df_all, region)
    if len(df_region) == 0:
        print(f"No schools found for region: {region}")
        return set(), set()

    t0 = time.time()
    df_edges, succeeded_ids, failed_ids = build_region_edges(
        df_region,
        haversine_cutoff_km=HAVERSINE_CUTOFF_KM,
        road_cutoff_m=ROAD_CUTOFF_M,
        osrm_url=osrm_url,
        region_name=region,
    )
    elapsed = time.time() - t0

    df_edges["source_region"] = region
    df_edges["is_cross_region"] = False

    EDGES_DIR.mkdir(parents=True, exist_ok=True)
    df_edges.to_parquet(out_path, index=False)

    # Save per-region status
    with open(status_path, "w") as f:
        json.dump({
            "region": region,
            "succeeded": sorted(succeeded_ids),
            "failed": sorted(failed_ids),
            "n_edges": len(df_edges),
            "elapsed_s": round(elapsed),
        }, f)

    print(f"Saved: {out_path.name} ({len(df_edges):,} edges, {elapsed:.0f}s)")
    return succeeded_ids, failed_ids


def run_cross_region(df_all, osrm_url, force=False):
    """Compute cross-region boundary edges."""
    cross_path = EDGES_DIR / "cross_region_pairs.parquet"

    if cross_path.exists() and not force:
        df_existing = pd.read_parquet(cross_path)
        print(f"Cross-region edges already exist ({len(df_existing):,} edges)")
        print(f"  Use --force to recompute.")
        return

    regions = list_regions(df_all)
    adjacent_pairs = _default_adjacent_regions()
    cross_edges = []

    for region_a, region_b in adjacent_pairs:
        if region_a not in regions or region_b not in regions:
            continue

        t0 = time.time()
        df_cross = build_cross_region_edges(
            df_all, region_a, region_b,
            haversine_cutoff_km=HAVERSINE_CUTOFF_KM,
            road_cutoff_m=ROAD_CUTOFF_M,
            osrm_url=osrm_url,
        )
        elapsed = time.time() - t0

        if len(df_cross) > 0:
            df_cross["is_cross_region"] = True
            src_regions = df_all.set_index("school_id")["region"]
            df_cross["source_region"] = df_cross["source_id"].map(src_regions)
            cross_edges.append(df_cross)
            print(f"  {region_a} ↔ {region_b}: {len(df_cross):,} edges ({elapsed:.0f}s)")

    if cross_edges:
        df_cross_all = pd.concat(cross_edges, ignore_index=True)
        df_cross_all = df_cross_all.drop_duplicates(
            subset=["source_id", "target_id"], keep="first"
        )
        EDGES_DIR.mkdir(parents=True, exist_ok=True)
        df_cross_all.to_parquet(cross_path, index=False)
        print(f"Saved: {cross_path.name} ({len(df_cross_all):,} edges)")
    else:
        print("No cross-region edges found.")


def finalize(df_all, upload=True):
    """Combine per-region parquets, generate manifest, optionally upload to GCS."""
    print("\n" + "=" * 60)
    print("FINALIZE: Combining edge files")
    print("=" * 60)

    # --- Build OSRM status from per-region status files ---
    all_succeeded = set()
    all_failed = set()
    status_files = sorted(EDGES_DIR.glob("region_*_status.json"))
    for sf in status_files:
        status = json.load(open(sf))
        all_succeeded.update(status.get("succeeded", []))
        all_failed.update(status.get("failed", []))
    # A school that succeeded in any region/batch is not failed
    all_failed -= all_succeeded

    print(f"OSRM status from {len(status_files)} region status files:")
    print(f"  Succeeded: {len(all_succeeded):,}")
    print(f"  Failed (all retries exhausted): {len(all_failed):,}")

    # --- Assign osrm_status to coordinate snapshot ---
    all_ids = set(df_all["school_id"])
    not_attempted = all_ids - all_succeeded - all_failed

    def _assign_status(sid):
        if sid in all_failed:
            return "osrm_failed"
        elif sid in all_succeeded:
            return "computed"
        else:
            return "not_attempted"

    df_all = df_all.copy()
    df_all["osrm_status"] = df_all["school_id"].map(_assign_status)

    snapshot_path = EDGES_DIR / "schools_unified_snapshot.parquet"
    df_all.to_parquet(snapshot_path, index=False)

    status_counts = df_all["osrm_status"].value_counts()
    print(f"\nosrm_status in coordinate snapshot:")
    for s, n in status_counts.items():
        print(f"  {s}: {n:,}")

    # --- Load and combine all edge files ---
    region_files = sorted(EDGES_DIR.glob("region_*.parquet"))
    cross_file = EDGES_DIR / "cross_region_pairs.parquet"

    edge_files = list(region_files)
    if cross_file.exists():
        edge_files.append(cross_file)

    if not edge_files:
        print("ERROR: No edge files found. Run --all first.")
        return

    print(f"\nCombining {len(edge_files)} edge files:")
    for f in edge_files:
        print(f"  {f.name}")

    dfs = [pd.read_parquet(f) for f in edge_files]
    df_edges = pd.concat(dfs, ignore_index=True)

    # Tag sea-separated edges
    df_edges = tag_sea_separated(df_edges)

    # --- Save combined ---
    combined_path = EDGES_DIR / "all_edges.parquet"
    df_edges.to_parquet(combined_path, index=False)

    # --- Statistics ---
    connected_ids = set(df_edges["source_id"]) | set(df_edges["target_id"])
    isolated = all_ids - connected_ids

    src_counts = df_edges.groupby("source_id").size()
    regions = list_regions(df_all)

    print(f"\nCombined edge table: {len(df_edges):,} edges")
    print(f"  File size: {combined_path.stat().st_size / 1e6:.1f} MB")
    print(f"  Within-region: {(~df_edges['is_cross_region']).sum():,}")
    print(f"  Cross-region:  {df_edges['is_cross_region'].sum():,}")
    print(f"  Sea-separated: {df_edges['is_sea_separated'].sum():,}")
    print(f"  Isolated schools: {len(isolated):,} / {len(all_ids):,}")
    print(f"  Mean edges/school: {src_counts.mean():.1f}")
    print(f"  Median edges/school: {src_counts.median():.0f}")

    # --- Manifest ---
    manifest = {
        "version": "v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "parameters": {
            "haversine_cutoff_km": HAVERSINE_CUTOFF_KM,
            "road_cutoff_m": ROAD_CUTOFF_M,
            "osrm_profile": "car",
        },
        "statistics": {
            "n_schools_total": len(df_all),
            "n_schools_public": int((df_all["sector"] == "public").sum()),
            "n_schools_private": int((df_all["sector"] == "private").sum()),
            "n_edges_total": len(df_edges),
            "n_edges_within_region": int((~df_edges["is_cross_region"]).sum()),
            "n_edges_cross_region": int(df_edges["is_cross_region"].sum()),
            "n_edges_sea_separated": int(df_edges["is_sea_separated"].sum()),
            "n_schools_isolated": len(isolated),
            "n_schools_osrm_computed": len(all_succeeded),
            "n_schools_osrm_failed": len(all_failed),
            "n_schools_not_attempted": len(not_attempted),
            "n_regions": len(regions),
            "regions": regions,
            "mean_edges_per_school": round(float(src_counts.mean()), 1),
            "median_edges_per_school": round(float(src_counts.median()), 1),
        },
        "files": {
            "combined": "all_edges.parquet",
            "per_region": [f.name for f in region_files],
            "cross_region": "cross_region_pairs.parquet" if cross_file.exists() else None,
            "coordinate_snapshot": "schools_unified_snapshot.parquet",
        },
    }

    manifest_path = EDGES_DIR / "_manifest.json"
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)
    print(f"\nSaved manifest: {manifest_path.name}")

    # --- GCS upload ---
    if upload:
        print("\nUploading to GCS...")
        for f in sorted(EDGES_DIR.glob("*.parquet")):
            upload_parquet(f, str(UGNAY_EDGES_DIR))
        upload_json(manifest_path, str(UGNAY_EDGES_DIR))
        upload_parquet(snapshot_path, str(UGNAY_COORDINATES_DIR))
        print("All files uploaded to GCS.")
    else:
        print("\nSkipping GCS upload (--no-upload).")

    print(f"\n{'=' * 60}")
    print("DONE. Verify results with: notebooks/1.1-validate-edges.ipynb")
    print(f"{'=' * 60}")


def main():
    parser = argparse.ArgumentParser(
        description="Compute sparse school-to-school distance network for the Philippines",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  Full pipeline:   python run_region_batch.py --all --cross-region --finalize
  Single region:   python run_region_batch.py "Region IV-A"
  Retry failed:    python run_region_batch.py "Region IV-A" --force
  Finalize only:   python run_region_batch.py --finalize
  List status:     python run_region_batch.py --list
        """,
    )
    parser.add_argument(
        "regions", nargs="*", help="Region names to process"
    )
    parser.add_argument(
        "--list", action="store_true", help="List available regions and exit"
    )
    parser.add_argument(
        "--all", action="store_true", help="Process all regions"
    )
    parser.add_argument(
        "--cross-region", action="store_true",
        help="Compute cross-region boundary edges"
    )
    parser.add_argument(
        "--finalize", action="store_true",
        help="Combine per-region files, generate manifest, upload to GCS"
    )
    parser.add_argument(
        "--no-upload", action="store_true",
        help="Skip GCS upload during --finalize"
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Recompute even if output already exists"
    )
    parser.add_argument(
        "--osrm-url", default=OSRM_URL, help="OSRM endpoint URL"
    )

    args = parser.parse_args()
    osrm_url = args.osrm_url

    # Load coordinates
    print("Loading school coordinates...")
    df_all = load_unified()
    df_all = tag_island_group(df_all)
    regions = list_regions(df_all)
    EDGES_DIR.mkdir(parents=True, exist_ok=True)

    if args.list:
        print(f"\nAvailable regions ({len(regions)}):")
        for r in regions:
            n = len(get_region_schools(df_all, r))
            safe = r.lower().replace(" ", "_").replace("-", "_")
            exists = (EDGES_DIR / f"region_{safe}.parquet").exists()
            status = " [done]" if exists else ""
            print(f"  {r}: {n:,} schools{status}")
        cross_exists = (EDGES_DIR / "cross_region_pairs.parquet").exists()
        print(f"\n  Cross-region: {'[done]' if cross_exists else '[pending]'}")
        combined_exists = (EDGES_DIR / "all_edges.parquet").exists()
        print(f"  Combined: {'[done]' if combined_exists else '[pending]'}")
        return

    # Determine what to compute
    needs_osrm = args.all or args.cross_region or bool(args.regions)

    if needs_osrm:
        if not check_osrm(osrm_url):
            sys.exit(1)

    if args.all:
        target_regions = regions
    elif args.regions:
        target_regions = args.regions
    else:
        target_regions = []

    # Run region computations
    t_start = time.time()
    for region in target_regions:
        if region not in regions:
            print(f"WARNING: Region '{region}' not found in data. Skipping.")
            continue
        run_region(df_all, region, osrm_url, force=args.force)

    # Run cross-region
    if args.cross_region:
        run_cross_region(df_all, osrm_url, force=args.force)

    # Finalize
    if args.finalize:
        finalize(df_all, upload=not args.no_upload)

    if needs_osrm:
        elapsed = time.time() - t_start
        print(f"\nTotal computation time: {elapsed / 60:.1f} minutes")

    if not target_regions and not args.cross_region and not args.finalize and not args.list:
        parser.print_help()


if __name__ == "__main__":
    main()
