#!/usr/bin/env python3
"""
Validation suite for the sparse edge table.

Runs self-consistency checks, coordinate-distance coherence tests, and
distribution stability comparisons after each pipeline run.

Usage:
    python validate_edges.py                          # run all tests
    python validate_edges.py --previous-manifest PATH # compare against previous run
    python validate_edges.py --sample-size 10000      # adjust sample size

Exit codes:
    0 — all tests passed
    1 — one or more tests failed
"""

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from modules.gcs_utils import OUTPUT_DIR

EDGES_DIR = OUTPUT_DIR / "edges"


def load_data():
    """Load edge table and manifest."""
    edges_path = EDGES_DIR / "all_edges.parquet"
    manifest_path = EDGES_DIR / "_manifest.json"

    if not edges_path.exists():
        print("ERROR: all_edges.parquet not found. Run Phase 1 first.")
        sys.exit(1)

    df = pd.read_parquet(edges_path)
    manifest = json.load(open(manifest_path)) if manifest_path.exists() else None

    print(f"Loaded {len(df):,} edges")
    return df, manifest


# =========================================================================
# Test 2: Self-consistency
# =========================================================================

def test_road_geq_haversine(df):
    """Road distance must be >= haversine distance (physically impossible otherwise)."""
    print("\n--- Test: road >= haversine ---")

    valid = df["road_haversine_ratio"].notna()
    below_one = df.loc[valid, "road_haversine_ratio"] < 0.99  # small tolerance for float precision
    n_violations = below_one.sum()
    n_tested = valid.sum()

    if n_violations == 0:
        print(f"  PASS: {n_tested:,} edges checked, all have road >= haversine")
        return True
    else:
        pct = n_violations / n_tested * 100
        print(f"  FAIL: {n_violations:,} / {n_tested:,} edges ({pct:.2f}%) have road < haversine")
        worst = df.loc[valid & below_one].nsmallest(5, "road_haversine_ratio")
        for _, r in worst.iterrows():
            print(f"    {r['source_id']} → {r['target_id']}: "
                  f"road={r['road_distance_m']:.0f}m, hav={r['haversine_distance_m']:.0f}m, "
                  f"ratio={r['road_haversine_ratio']:.3f}")
        return False


def test_symmetry(df, sample_size=10000, threshold=0.5):
    """
    A→B and B→A distances should be similar.
    Large asymmetry (>50% difference) flags routing anomalies.
    """
    print("\n--- Test: distance symmetry ---")

    # Sample edges and look for their reverse
    sample = df.sample(min(sample_size, len(df)), random_state=42)

    # Build lookup for reverse check
    reverse_lookup = df.set_index(["source_id", "target_id"])["road_distance_m"]

    asymmetric = 0
    checked = 0
    worst_cases = []

    for _, row in sample.iterrows():
        key = (row["target_id"], row["source_id"])
        if key in reverse_lookup.index:
            checked += 1
            d_ab = row["road_distance_m"]
            d_ba = reverse_lookup[key]
            if isinstance(d_ba, pd.Series):
                d_ba = d_ba.iloc[0]
            avg = (d_ab + d_ba) / 2
            if avg > 0:
                diff = abs(d_ab - d_ba) / avg
                if diff > threshold:
                    asymmetric += 1
                    worst_cases.append((row["source_id"], row["target_id"],
                                       d_ab, d_ba, diff))

    if checked == 0:
        print(f"  SKIP: no reverse pairs found in sample")
        return True

    pct = asymmetric / checked * 100
    passed = pct < 1.0  # less than 1% should be highly asymmetric

    if passed:
        print(f"  PASS: {checked:,} pairs checked, {asymmetric} asymmetric ({pct:.2f}%)")
    else:
        print(f"  FAIL: {checked:,} pairs checked, {asymmetric} asymmetric ({pct:.2f}%)")

    if worst_cases:
        worst_cases.sort(key=lambda x: -x[4])
        print(f"  Worst asymmetries:")
        for src, tgt, d_ab, d_ba, diff in worst_cases[:5]:
            print(f"    {src} → {tgt}: {d_ab/1000:.1f} km, reverse: {d_ba/1000:.1f} km, diff: {diff*100:.0f}%")

    return passed


def test_triangle_inequality(df, sample_size=5000):
    """
    For sampled triples A, B, C: dist(A,C) <= dist(A,B) + dist(B,C).
    Violations indicate data corruption or severe routing errors.
    """
    print("\n--- Test: triangle inequality ---")

    # Build a lookup dict for fast access
    lookup = {}
    for _, row in df.iterrows():
        src = row["source_id"]
        if src not in lookup:
            lookup[src] = {}
        lookup[src][row["target_id"]] = row["road_distance_m"]

    # Sample source schools that have neighbors
    sources = list(lookup.keys())
    np.random.seed(42)
    sampled_sources = np.random.choice(sources, min(sample_size, len(sources)), replace=False)

    violations = 0
    checked = 0

    for a in sampled_sources:
        neighbors_a = lookup.get(a, {})
        if len(neighbors_a) < 2:
            continue

        # Pick two neighbors of A
        neighbor_ids = list(neighbors_a.keys())
        b = np.random.choice(neighbor_ids)
        remaining = [n for n in neighbor_ids if n != b]
        if not remaining:
            continue
        c = np.random.choice(remaining)

        d_ab = neighbors_a[b]
        d_ac = neighbors_a[c]

        # Check if B→C exists
        d_bc = lookup.get(b, {}).get(c, None)
        if d_bc is None:
            continue

        checked += 1
        # Triangle inequality: d_ac <= d_ab + d_bc (with 1% tolerance)
        if d_ac > (d_ab + d_bc) * 1.01:
            violations += 1

    if checked == 0:
        print(f"  SKIP: no valid triples found")
        return True

    pct = violations / checked * 100
    passed = pct < 0.5  # less than 0.5% violations expected

    if passed:
        print(f"  PASS: {checked:,} triples checked, {violations} violations ({pct:.2f}%)")
    else:
        print(f"  FAIL: {checked:,} triples checked, {violations} violations ({pct:.2f}%)")

    return passed


# =========================================================================
# Test 3: Distribution stability
# =========================================================================

def test_distribution_stability(manifest, previous_manifest_path):
    """Compare current manifest statistics against a previous run."""
    print("\n--- Test: distribution stability ---")

    if previous_manifest_path is None or not Path(previous_manifest_path).exists():
        print(f"  SKIP: no previous manifest provided")
        return True

    if manifest is None:
        print(f"  SKIP: no current manifest")
        return True

    prev = json.load(open(previous_manifest_path))
    curr_stats = manifest.get("statistics", {})
    prev_stats = prev.get("statistics", {})

    passed = True
    checks = [
        ("n_edges_total", 0.10),       # allow 10% change
        ("n_schools_total", 0.05),     # allow 5% change
        ("n_schools_isolated", 0.50),  # allow 50% change (sensitive to coord fixes)
        ("mean_edges_per_school", 0.15),
    ]

    for key, max_change in checks:
        curr_val = curr_stats.get(key)
        prev_val = prev_stats.get(key)
        if curr_val is None or prev_val is None or prev_val == 0:
            print(f"  SKIP: {key} not in both manifests")
            continue

        change = abs(curr_val - prev_val) / prev_val
        status = "PASS" if change <= max_change else "FAIL"
        if status == "FAIL":
            passed = False
        print(f"  {status}: {key}: {prev_val:,} → {curr_val:,} ({change*100:+.1f}%, threshold: {max_change*100:.0f}%)")

    return passed


# =========================================================================
# Test 4: Coordinate-distance coherence
# =========================================================================

def test_ratio_distribution(df, sample_size=50000):
    """
    Road-to-haversine ratio should be between 1.0 and ~5.0 for nearly all edges.
    Ratios > 10x or < 1.0 indicate bad coordinates or OSRM errors.
    """
    print("\n--- Test: road/haversine ratio distribution ---")

    sample = df.dropna(subset=["road_haversine_ratio"]).sample(
        min(sample_size, len(df)), random_state=42
    )
    ratio = sample["road_haversine_ratio"]

    below_one = (ratio < 0.99).sum()
    above_five = (ratio > 5.0).sum()
    above_ten = (ratio > 10.0).sum()
    above_fifty = (ratio > 50.0).sum()

    n = len(ratio)
    pct_above_ten = above_ten / n * 100

    print(f"  Sample: {n:,} edges")
    print(f"  Median ratio: {ratio.median():.2f}x")
    print(f"  p95: {ratio.quantile(0.95):.2f}x, p99: {ratio.quantile(0.99):.2f}x")
    print(f"  Below 1.0 (impossible): {below_one:,} ({below_one/n*100:.2f}%)")
    print(f"  Above 5.0 (extreme): {above_five:,} ({above_five/n*100:.2f}%)")
    print(f"  Above 10.0 (suspicious): {above_ten:,} ({above_ten/n*100:.2f}%)")
    print(f"  Above 50.0 (likely bad coord): {above_fifty:,} ({above_fifty/n*100:.2f}%)")

    # Fail if >0.1% of edges have ratio > 10x
    passed = pct_above_ten < 0.1 and below_one == 0

    if passed:
        print(f"  PASS")
    else:
        if below_one > 0:
            print(f"  FAIL: {below_one} edges with ratio < 1.0")
        if pct_above_ten >= 0.1:
            print(f"  FAIL: {pct_above_ten:.2f}% of edges have ratio > 10x")

        # Show worst cases
        extreme = df.nlargest(5, "road_haversine_ratio")
        print(f"  Worst ratios:")
        for _, r in extreme.iterrows():
            print(f"    {r['source_id']} → {r['target_id']}: "
                  f"road={r['road_distance_m']/1000:.1f}km, "
                  f"hav={r['haversine_distance_m']/1000:.1f}km, "
                  f"ratio={r['road_haversine_ratio']:.1f}x")

    return passed


# =========================================================================
# Main
# =========================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Validate sparse edge table after pipeline run"
    )
    parser.add_argument(
        "--previous-manifest", type=str, default=None,
        help="Path to previous _manifest.json for distribution stability comparison"
    )
    parser.add_argument(
        "--sample-size", type=int, default=10000,
        help="Sample size for symmetry and triangle inequality tests"
    )
    args = parser.parse_args()

    df, manifest = load_data()

    t0 = time.time()
    results = {}

    # Test 2: Self-consistency
    results["road_geq_haversine"] = test_road_geq_haversine(df)
    results["symmetry"] = test_symmetry(df, sample_size=args.sample_size)
    results["triangle_inequality"] = test_triangle_inequality(df, sample_size=args.sample_size)

    # Test 3: Distribution stability
    results["distribution_stability"] = test_distribution_stability(
        manifest, args.previous_manifest
    )

    # Test 4: Coordinate-distance coherence
    results["ratio_distribution"] = test_ratio_distribution(df)

    # Summary
    elapsed = time.time() - t0
    n_pass = sum(v for v in results.values())
    n_total = len(results)
    n_fail = n_total - n_pass

    print(f"\n{'='*60}")
    print(f"VALIDATION SUMMARY ({elapsed:.1f}s)")
    print(f"{'='*60}")
    for name, passed in results.items():
        status = "PASS" if passed else "FAIL"
        print(f"  [{status}] {name}")
    print(f"\n  {n_pass}/{n_total} passed, {n_fail} failed")

    sys.exit(0 if n_fail == 0 else 1)


if __name__ == "__main__":
    main()
