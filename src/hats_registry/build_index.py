"""Regenerate `registry/_index.json`, or validate the registry without
writing anything (`--check`).

Run by CI in two places:
  - On every push to `main` touching `registry/**` (rebuilds and commits
    the index; see .github/workflows/update-registry-index.yml).
  - On every pull request touching `registry/**` (validates only, via
    `--check`, so a broken entry fails the PR before merge rather than
    only being caught once it lands on main; see
    .github/workflows/validate-registry-pr.yml).

The index is a flat manifest of every core/extension file's path, relative
to the `registry/` directory — it exists so that `HatsRegistry.load()` can
fetch registry contents via raw.githubusercontent.com (effectively
unlimited) instead of the GitHub REST API's tree-listing endpoint (60
requests/hour when unauthenticated).

Usage:
    python scripts/build_index.py [registry_root]            # write _index.json
    python scripts/build_index.py [registry_root] --check    # validate only

Exits non-zero if the registry tree fails validation. In write mode, the
previous index is left untouched on failure — a broken registry never gets
published to _index.json.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Allow running directly from a checkout without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from hats_registry.registry import (  # noqa: E402
    HatsRegistry,
    INDEX_FILENAME,
    RegistryValidationError,
)


def build_index(registry_root: Path) -> dict[str, list[str]]:
    """Validate the registry tree, then return the index payload.

    Raises RegistryValidationError (via HatsRegistry.from_directory) if the
    tree is structurally invalid: a filename/catalog_id mismatch, or an
    extension whose `extends` doesn't resolve to an existing core.
    """
    HatsRegistry.from_directory(registry_root, validate=True)

    core_paths = sorted(
        str(p.relative_to(registry_root))
        for p in registry_root.glob("*/core.json")
    )
    ext_paths = sorted(
        str(p.relative_to(registry_root))
        for p in registry_root.glob("*/extensions/*.json")
    )
    return {"cores": core_paths, "extensions": ext_paths}


def main() -> None:
    args = sys.argv[1:]
    check_only = "--check" in args
    positional = [a for a in args if a != "--check"]
    registry_root = Path(positional[0]) if positional else Path("registry")

    try:
        index = build_index(registry_root)
    except RegistryValidationError as exc:
        print(f"Registry validation failed: {exc}", file=sys.stderr)
        sys.exit(1)

    summary = (
        f"{len(index['cores'])} core, {len(index['extensions'])} extension entries"
    )

    if check_only:
        print(f"Registry OK ({summary}).")
        return

    out_path = registry_root / INDEX_FILENAME
    out_path.write_text(json.dumps(index, indent=2) + "\n")
    print(f"Wrote {out_path} ({summary})")


if __name__ == "__main__":
    main()