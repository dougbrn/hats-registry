"""Generate static test fixtures for hats-registry.

Produces:
  - A toy core catalog (via lsdb.generate_catalog), written to two mirror
    locations ("primary" and "sdf" -- arbitrary labels, matching the mirror
    convention from the registry schema). The core is written WITHOUT its
    `a` column (see SUBSET_EXT_ID below) even though `a` exists on the
    in-memory generated catalog -- deliberately, so that `a` is a genuinely
    new column once the subset extension adds it back, rather than a
    column that already existed on the core and just gets duplicated.
  - Two toy extension catalogs derived from the core, each also mirrored to
    both locations:
      * `<core>_subset`   -- ra, dec, plus `a` -- the column deliberately
        left off the written core (see above), demonstrating an extension
        adding a column the core doesn't have, not just re-supplying one
        it already does.
      * `<core>_derived`  -- ra, dec, plus a value computed from the core
        via a simple example function, standing in for a real value-add
        catalog (e.g. photo-z).
    Neither extension includes an `id` column -- HATS/the registry have no
    standardized concept of an identifier column (unlike ra/dec), so it's
    left out entirely rather than exercising a code path that doesn't
    treat it specially anyway.
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

# Repo root, resolved relative to this script's own location -- not the
# caller's current working directory. This makes `python
# scripts/generate_test_fixtures.py` produce the same output regardless of
# whether it's invoked from the repo root or from inside scripts/.
REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent

# Where the generated fixture tree lands, relative to the repo root. Both
# the catalog data and the registry/ entries describing it are written
# under here, self-contained, so tests can point HatsRegistry.from_directory
# and lsdb.open_catalog at subpaths of a single fixture root.
OUTPUT_ROOT = REPO_ROOT / "tests" / "data" / "fixture_registry"

CORE_ID = "test_core"
SUBSET_EXT_ID = "test_core_subset"
DERIVED_EXT_ID = "test_core_derived"

MIRROR_LABELS = ["primary", "sdf"]

# `description` isn't a formal schema field yet (see registry.py --
# CatalogEntryBase's extra="allow"), so this is exercised deliberately on
# only one of the two extensions: it lets tests/display code cover both
# "description present" and "description absent" without a third fixture
# entry.
SUBSET_DESCRIPTION = (
    "A simple extension adding the `a` column (ra, dec, a)"
)


def _derive_value(core_df):
    """Stand-in for a real derived/value-add computation (e.g. a photo-z
    estimate) -- deliberately trivial since the fixture only needs a
    distinct, checkable column, not a realistic algorithm. Operates on the
    full in-memory core dataframe (including `a`, even though `a` is
    dropped from the core catalog actually written to disk -- see
    generate()) since the computation itself is free to use whatever the
    core had available at generation time.
    """
    return core_df["a"] * 2.0 + core_df["b"]


def generate() -> None:
    if OUTPUT_ROOT.exists():
        shutil.rmtree(OUTPUT_ROOT)
    catalogs_root = OUTPUT_ROOT / "catalogs"
    registry_root = OUTPUT_ROOT / "registry"

    # --- Core catalog ---------------------------------------------------
    # `a` stays out of what's actually written for the core -- kept only
    # in-memory (via `core`, below) so the subset extension can add it back
    # as a genuinely new column. Because of that, extensions are derived
    # from this in-memory catalog rather than by reopening the written
    # core from disk (which deliberately no longer has `a`).
    core = lsdb.generate_catalog(n_base=50, n_layer=5, seed=42, catalog_name=CORE_ID)
    core_without_a = core.drop(columns=["a"])
    core_paths = {}
    for mirror in MIRROR_LABELS:
        out_dir = catalogs_root / mirror / CORE_ID
        core_without_a.write_catalog(
            out_dir,
            catalog_name=CORE_ID,
            overwrite=True,
            progress_bar=False,
            as_collection=False,
            addl_hats_properties={"hats_registry_id": CORE_ID},
        )
        core_paths[mirror] = str(out_dir.relative_to(OUTPUT_ROOT))

    core_df = core[["ra", "dec", "a", "b"]].compute()

    # --- Extension 1: adds `a`, a column the written core doesn't have ---
    subset_df = core_df[["ra", "dec", "a"]]
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
    derived_df = core_df[["ra", "dec"]].copy()
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
                "description": SUBSET_DESCRIPTION,
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