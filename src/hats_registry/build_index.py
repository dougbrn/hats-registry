
"""Regenerate `registry/_index.json`.
 
Run by CI on every merge to `main` (see .github/workflows/build-index.yml).
The index is a flat manifest of every core/extension file's path, relative
to the `registry/` directory — it exists so that `HatsRegistry.load()` can
fetch registry contents via raw.githubusercontent.com (effectively
unlimited) instead of the GitHub REST API's tree-listing endpoint (60
requests/hour when unauthenticated).
 
Usage:
    python scripts/build_index.py [registry_root]
 
Exits non-zero (and leaves the previous index untouched) if the registry
tree fails validation, so a broken registry never gets published to
_index.json.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Allow running directly from a checkout without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from hats_registry.registry import HatsRegistry, INDEX_FILENAME  # noqa: E402


def build_index(registry_root: Path) -> dict[str, list[str]]:
    """Validate the registry tree, then return the index payload."""
    # Loading via from_directory with validate=True doubles as the
    # structural check: filename/catalog_id agreement and dangling
    # `extends` references both raise before we'd ever write a bad index.
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
    registry_root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("registry")
    index = build_index(registry_root)

    out_path = registry_root / INDEX_FILENAME
    out_path.write_text(json.dumps(index, indent=2) + "\n")
    print(
        f"Wrote {out_path} "
        f"({len(index['cores'])} core, {len(index['extensions'])} extension entries)"
    )


if __name__ == "__main__":
    main()
