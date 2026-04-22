#!/usr/bin/env python3
"""
Compute per-school accessibility metrics and administrative aggregations
from the sparse edge table.

Usage:
    python run_metrics.py                    # compute metrics + aggregations
    python run_metrics.py --no-upload        # skip GCS upload
    python run_metrics.py --edges-dir PATH   # custom edges directory

Prerequisite: Phase 1 edge computation must be complete (all_edges.parquet).
"""

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from modules.accessibility_metrics import compute_metrics
from modules.aggregation import aggregate_all
from modules.gcs_utils import (
    OUTPUT_DIR,
    UGNAY_METRICS_DIR,
    UGNAY_AGGREGATIONS_DIR,
    upload_parquet,
    upload_json,
)


def main():
    parser = argparse.ArgumentParser(
        description="Compute school accessibility metrics and aggregations"
    )
    parser.add_argument(
        "--edges-dir", type=Path, default=OUTPUT_DIR / "edges",
        help="Directory containing Phase 1 edge outputs"
    )
    parser.add_argument(
        "--no-upload", action="store_true",
        help="Skip GCS upload"
    )
    args = parser.parse_args()

    edges_dir = args.edges_dir
    metrics_dir = OUTPUT_DIR / "metrics"
    aggregations_dir = OUTPUT_DIR / "aggregations"
    metrics_dir.mkdir(parents=True, exist_ok=True)
    aggregations_dir.mkdir(parents=True, exist_ok=True)

    # --- Load Phase 1 outputs ---
    edges_path = edges_dir / "all_edges.parquet"
    schools_path = edges_dir / "schools_unified_snapshot.parquet"

    if not edges_path.exists():
        print(f"ERROR: {edges_path} not found. Run Phase 1 first.")
        sys.exit(1)

    print("Loading Phase 1 outputs...")
    df_edges = pd.read_parquet(edges_path)
    df_schools = pd.read_parquet(schools_path)
    print(f"  Edges: {len(df_edges):,}")
    print(f"  Schools: {len(df_schools):,}")

    # --- Compute per-school metrics ---
    print("\nComputing per-school accessibility metrics...")
    t0 = time.time()
    df_metrics = compute_metrics(df_edges, df_schools)
    elapsed = time.time() - t0
    print(f"  Done in {elapsed:.1f}s")

    # Save
    metrics_path = metrics_dir / "school_accessibility.parquet"
    df_metrics.to_parquet(metrics_path, index=False)
    print(f"  Saved: {metrics_path.name} ({len(df_metrics):,} rows, {metrics_path.stat().st_size / 1e6:.1f} MB)")

    # Summary
    print(f"\n  Isolation score: mean={df_metrics['isolation_score'].mean():.4f}, median={df_metrics['isolation_score'].median():.4f}")
    print(f"  Private deserts: {df_metrics['private_desert'].sum():,} schools ({df_metrics['private_desert'].mean()*100:.1f}%)")
    print(f"  ESC deserts: {df_metrics['esc_desert'].sum():,} schools ({df_metrics['esc_desert'].mean()*100:.1f}%)")
    print(f"  Nearest private (median): {df_metrics['nearest_private_km'].replace(float('inf'), float('nan')).median():.1f} km")
    print(f"  Nearest ESC (median): {df_metrics['nearest_esc_km'].replace(float('inf'), float('nan')).median():.1f} km")

    # --- Compute aggregations ---
    print("\nComputing administrative aggregations...")
    t0 = time.time()
    aggregations = aggregate_all(df_metrics)
    elapsed = time.time() - t0
    print(f"  Done in {elapsed:.1f}s")

    for level, df_agg in aggregations.items():
        agg_path = aggregations_dir / f"{level}_summary.parquet"
        df_agg.to_parquet(agg_path, index=False)
        print(f"  Saved: {agg_path.name} ({len(df_agg):,} rows)")

    # --- Manifest ---
    manifest = {
        "version": "v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "phase_1_manifest": str(edges_dir / "_manifest.json"),
        "outputs": {
            "school_accessibility": {
                "file": "school_accessibility.parquet",
                "rows": len(df_metrics),
                "columns": list(df_metrics.columns),
            },
            "aggregations": {
                level: {
                    "file": f"{level}_summary.parquet",
                    "rows": len(df_agg),
                }
                for level, df_agg in aggregations.items()
            },
        },
        "statistics": {
            "n_schools": len(df_metrics),
            "n_private_desert": int(df_metrics["private_desert"].sum()),
            "n_esc_desert": int(df_metrics["esc_desert"].sum()),
            "pct_private_desert": round(float(df_metrics["private_desert"].mean() * 100), 1),
            "pct_esc_desert": round(float(df_metrics["esc_desert"].mean() * 100), 1),
            "mean_isolation_score": round(float(df_metrics["isolation_score"].mean()), 4),
            "median_nearest_private_km": round(float(df_metrics["nearest_private_km"].replace(float("inf"), float("nan")).median()), 1),
            "median_nearest_esc_km": round(float(df_metrics["nearest_esc_km"].replace(float("inf"), float("nan")).median()), 1),
            "n_municipal_units": len(aggregations["municipal"]),
            "n_provincial_units": len(aggregations["provincial"]),
            "n_regional_units": len(aggregations["regional"]),
        },
    }

    manifest_path = metrics_dir / "_manifest.json"
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)
    print(f"\n  Saved manifest: {manifest_path.name}")

    # --- GCS upload ---
    if not args.no_upload:
        print("\nUploading to GCS...")
        upload_parquet(metrics_path, str(UGNAY_METRICS_DIR))
        upload_json(manifest_path, str(UGNAY_METRICS_DIR))
        for level, df_agg in aggregations.items():
            agg_path = aggregations_dir / f"{level}_summary.parquet"
            upload_parquet(agg_path, str(UGNAY_AGGREGATIONS_DIR))
        print("All files uploaded to GCS.")
    else:
        print("\nSkipping GCS upload (--no-upload).")

    print(f"\n{'='*60}")
    print("DONE. Verify results with: notebooks/2.0-validate-metrics.ipynb")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
