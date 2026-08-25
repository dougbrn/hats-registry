"""Generate static test fixtures for hats-registry.

Produces:
  - A toy core catalog (via lsdb.generate_catalog), written to two mirror
    locations ("primary" and "sdf" -- arbitrary labels, matching the mirror
    convention from the registry schema).
  - Two toy extension catalogs derived from the core, each also mirrored to
    both locations:
      * `<core>_subset`   -- a plain column peel-off (ra, dec, id, a).
      * `<core>_derived`  -- ra, dec, id plus a value computed from the core
        via a simple example function, standing in for a real value-add
        catalog (e.g. photo-z).
  - Matching `registry/<catalog_id>/core.json` and
    `registry/<catalog_id>/extensions/<ext_id>.json` entries, with `paths`
    dicts pointing at the mirrored locations relative to the fixture root.

This is a generation script, not a test itself, and its output is meant to
be committed to the repo (see OUTPUT_ROOT below) rather than regenerated on
every test run -- tests should load the committed fixture directly via
HatsRegistry.from_directory() / lsdb.open_catalog(), keeping test runs fast
and independent of lsdb/hats' generation internals staying stable release
to release. Re-run this script by hand (and re-commit the output) if the
fixture's shape needs to change.

Usage:
    python scripts/generate_test_fixtures.py
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import lsdb

# run from root
OUTPUT_ROOT = Path("tests/data/fixture_registry")

CORE_ID = "test_core"
SUBSET_EXT_ID = "test_core_subset"
DERIVED_EXT_ID = "test_core_derived"

MIRROR_LABELS = ["primary", "sdf"]


def _derive_value(core_df):
    """Stand-in for a real derived/value-add computation (e.g. a photo-z
    estimate) -- deliberately trivial since the fixture only needs a
    distinct, checkable column, not a realistic algorithm.
    """
    return core_df["a"] * 2.0 + core_df["b"]


def generate() -> None:
    if OUTPUT_ROOT.exists():
        shutil.rmtree(OUTPUT_ROOT)
    catalogs_root = OUTPUT_ROOT / "catalogs"
    registry_root = OUTPUT_ROOT / "registry"

    # --- Core catalog ---------------------------------------------------
    core = lsdb.generate_catalog(n_base=50, n_layer=5, seed=42, catalog_name=CORE_ID)
    core_paths = {}
    for mirror in MIRROR_LABELS:
        out_dir = catalogs_root / mirror / CORE_ID
        core.write_catalog(
            out_dir,
            catalog_name=CORE_ID,
            overwrite=True,
            progress_bar=False,
            as_collection=False,
            addl_hats_properties={"hats_registry_id": CORE_ID},
        )
        core_paths[mirror] = str(out_dir.relative_to(OUTPUT_ROOT))

    # Re-open from the primary mirror to derive extensions from -- exercises
    # the same read path a real extension author would use, rather than
    # reusing the in-memory `core` object directly.
    core_reopened = lsdb.open_catalog(str(catalogs_root / "primary" / CORE_ID))
    core_df = core_reopened[["ra", "dec", "id", "a", "b"]].compute()

    # --- Extension 1: plain column subset --------------------------------
    subset_df = core_df[["ra", "dec", "id", "a"]]
    subset_cat = lsdb.from_dataframe(
        subset_df, catalog_name=SUBSET_EXT_ID, ra_column="ra", dec_column="dec"
    )
    subset_paths = {}
    for mirror in MIRROR_LABELS:
        out_dir = catalogs_root / mirror / SUBSET_EXT_ID
        subset_cat.write_catalog(
            out_dir,
            catalog_name=SUBSET_EXT_ID,
            overwrite=True,
            progress_bar=False,
            as_collection=False,
            addl_hats_properties={"hats_registry_id": SUBSET_EXT_ID},
        )
        subset_paths[mirror] = str(out_dir.relative_to(OUTPUT_ROOT))

    # --- Extension 2: derived value via an example function ---------------
    derived_df = core_df[["ra", "dec", "id"]].copy()
    derived_df["derived_value"] = _derive_value(core_df)
    derived_cat = lsdb.from_dataframe(
        derived_df, catalog_name=DERIVED_EXT_ID, ra_column="ra", dec_column="dec"
    )
    derived_paths = {}
    for mirror in MIRROR_LABELS:
        out_dir = catalogs_root / mirror / DERIVED_EXT_ID
        derived_cat.write_catalog(
            out_dir,
            catalog_name=DERIVED_EXT_ID,
            overwrite=True,
            progress_bar=False,
            as_collection=False,
            addl_hats_properties={"hats_registry_id": DERIVED_EXT_ID},
        )
        derived_paths[mirror] = str(out_dir.relative_to(OUTPUT_ROOT))

    # --- Registry entries --------------------------------------------------
    core_dir = registry_root / CORE_ID
    core_dir.mkdir(parents=True, exist_ok=True)
    (core_dir / "core.json").write_text(
        json.dumps(
            {"catalog_id": CORE_ID, "catalog_type": "core", "paths": core_paths},
            indent=2,
        )
        + "\n"
    )

    ext_dir = core_dir / "extensions"
    ext_dir.mkdir(parents=True, exist_ok=True)
    (ext_dir / f"{SUBSET_EXT_ID}.json").write_text(
        json.dumps(
            {
                "catalog_id": SUBSET_EXT_ID,
                "catalog_type": "extension",
                "extends": CORE_ID,
                "modality": "tabular",
                "paths": subset_paths,
            },
            indent=2,
        )
        + "\n"
    )
    (ext_dir / f"{DERIVED_EXT_ID}.json").write_text(
        json.dumps(
            {
                "catalog_id": DERIVED_EXT_ID,
                "catalog_type": "extension",
                "extends": CORE_ID,
                "modality": "tabular",
                "paths": derived_paths,
            },
            indent=2,
        )
        + "\n"
    )

    print(f"Wrote fixture registry + catalogs under {OUTPUT_ROOT}/")


if __name__ == "__main__":
    generate()