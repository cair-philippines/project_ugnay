"""
Shared GCS utilities and project-level constants for project_ugnay.

Adapted from paaral_eda/modules/gcs_utils.py. Uses the same service account
and bucket (data_ecair_paaral) but writes under the ugnay/ prefix.
"""

import subprocess
from pathlib import Path


# ---------------------------------------------------------------------------
# Project-level paths
# ---------------------------------------------------------------------------
PROJECT_DIR = Path(__file__).resolve().parent.parent
KEY_DIR = PROJECT_DIR / "keys"
OUTPUT_DIR = PROJECT_DIR / "output"

# ---------------------------------------------------------------------------
# GCS bucket paths
# ---------------------------------------------------------------------------
BUCKET = "data_ecair_paaral"
UGNAY_DIR = Path(BUCKET) / "ugnay"
UGNAY_EDGES_DIR = UGNAY_DIR / "v1" / "edges"
UGNAY_METRICS_DIR = UGNAY_DIR / "v1" / "metrics"
UGNAY_AGGREGATIONS_DIR = UGNAY_DIR / "v1" / "aggregations"
UGNAY_COORDINATES_DIR = UGNAY_DIR / "v1" / "coordinates"
UGNAY_METADATA_DIR = UGNAY_DIR / "v1" / "metadata"

# ---------------------------------------------------------------------------
# GCS filesystem — initialized lazily via get_fs()
# ---------------------------------------------------------------------------
_fs = None


def get_fs():
    """Return a cached GCSFileSystem instance, installing gcsfs if needed."""
    global _fs
    if _fs is not None:
        return _fs

    try:
        import gcsfs
    except ImportError:
        print("Installing gcsfs library...")
        subprocess.run(["pip", "install", "-qq", "gcsfs"], check=True)
        print("gcsfs library installed.")
        import gcsfs

    path_tkn = KEY_DIR / "ecair-paaral-project-6178d521167f.json"
    if not path_tkn.exists():
        raise FileNotFoundError(
            f"GCS service token not found at {path_tkn}. "
            "Ensure the keys/ symlink points to paaral_eda/keys/."
        )

    _fs = gcsfs.GCSFileSystem(token=str(path_tkn))
    return _fs


def upload_parquet(local_path, gcs_dir):
    """Upload a local Parquet file to a GCS directory."""
    fs = get_fs()
    filename = Path(local_path).name
    gcs_path = str(Path(gcs_dir) / filename)
    fs.put(str(local_path), gcs_path)
    print(f"Uploaded {filename} → gs://{gcs_path}")
    return gcs_path


def upload_json(local_path, gcs_dir):
    """Upload a local JSON file to a GCS directory."""
    fs = get_fs()
    filename = Path(local_path).name
    gcs_path = str(Path(gcs_dir) / filename)
    fs.put(str(local_path), gcs_path)
    print(f"Uploaded {filename} → gs://{gcs_path}")
    return gcs_path
