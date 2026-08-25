"""Core data models and read-only discovery API for the HATS registry.

The registry itself is a directory of JSON files (see `registry/` at the
repo root) — this module is purely a reader/indexer over that data. Nothing
here writes to the registry; adding or editing catalogs is a git/PR
operation, not an API call.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal, Optional, Union
from urllib.parse import urlparse

import requests
from pydantic import BaseModel, Field, TypeAdapter, field_validator
from typing_extensions import Annotated

__all__ = [
    "CoreCatalogEntry",
    "ExtensionCatalogEntry",
    "CatalogEntry",
    "HatsRegistry",
    "set_default_ref",
    "get_default_ref",
]

# Default source of truth: the hats-registry GitHub repo itself. Passing a
# different `ref` (tag, branch, or commit SHA) to `HatsRegistry.load()` is
# how a caller gets reproducibility instead of always tracking `main`.
#
# Note this `ref` is orthogonal to the installed hats-registry *package*
# version (pinned normally via pip): it's the git ref of the *data* --
# the registry/ JSON tree -- fetched at runtime. A pip-pinned package
# version can still default to querying the live "main" data, the same
# way a database client library version is independent of which database
# state it happens to query. Not circular, just two separate axes of the
# same repo.
DEFAULT_OWNER = "dougbrn"
DEFAULT_REPO = "hats-registry"
DEFAULT_REF = "main"
DEFAULT_REGISTRY_SUBDIR = "registry"
INDEX_FILENAME = "_index.json"

# Reserved mirror key: every entry's `paths` dict must include this key, and
# it's the fallback used when a caller asks for a mirror that doesn't have a
# co-located copy of this particular catalog.
PRIMARY_MIRROR = "primary"

_default_ref = DEFAULT_REF


def set_default_ref(ref: str) -> None:
    """Set the ref (branch/tag/commit) that `HatsRegistry.load()` reads from
    when `ref` isn't passed explicitly. Useful to pin an entire session or
    application to a specific registry snapshot in one call, rather than
    passing `ref=...` to every `load()` call individually.

    This is process-global, mutable state -- fine for a notebook or a
    single-purpose script, but be aware of that if used from a library
    consumed by others (e.g. within LSDB itself), since it affects every
    caller's default in that process, not just the caller who set it.
    """
    global _default_ref
    _default_ref = ref


def get_default_ref() -> str:
    """Return the ref that `HatsRegistry.load()` currently defaults to."""
    return _default_ref

_CORE_PATH_RE = re.compile(r"^(?:.+/)?(?P<core_id>[^/]+)/core\.json$")
_EXTENSION_PATH_RE = re.compile(
    r"^(?:.+/)?(?P<core_id>[^/]+)/extensions/(?P<ext_id>[^/]+)\.json$"
)


class CatalogEntryBase(BaseModel):
    """Fields common to every registry entry."""

    catalog_id: str
    paths: dict[str, str]
    """Mirror label -> location URI. Must contain a `"primary"` entry
    (`PRIMARY_MIRROR`), which is used whenever a caller doesn't ask for a
    specific mirror, or asks for one this entry doesn't have a copy at.
    Any other key is a free-form mirror label -- e.g. "sdf" -- shared
    between a core entry and its co-located extensions to mean "these are
    the same physical copy of the data." There's no registry-enforced list
    of valid mirror labels; consistency across entries is a convention,
    checked by CI (see build_index.py) rather than the schema itself.
    """

    @field_validator("paths")
    @classmethod
    def _require_primary(cls, paths: dict[str, str]) -> dict[str, str]:
        if PRIMARY_MIRROR not in paths:
            raise ValueError(f"paths must include a '{PRIMARY_MIRROR}' entry")
        return paths

    def resolve_path(self, mirror: Optional[str] = None) -> str:
        """Return this entry's location for the given mirror label, falling
        back to the primary location if `mirror` is None or isn't a mirror
        this entry has a copy at.
        """
        if mirror is not None and mirror in self.paths:
            return self.paths[mirror]
        return self.paths[PRIMARY_MIRROR]


class CoreCatalogEntry(CatalogEntryBase):
    """A registered core HATS catalog."""

    catalog_type: Literal["core"] = "core"


class ExtensionCatalogEntry(CatalogEntryBase):
    """A registered extension catalog, tied to a core catalog by ID."""

    catalog_type: Literal["extension"] = "extension"
    extends: str
    modality: Optional[str] = None


CatalogEntry = Annotated[
    Union[CoreCatalogEntry, ExtensionCatalogEntry],
    Field(discriminator="catalog_type"),
]

_entry_adapter: TypeAdapter = TypeAdapter(CatalogEntry)


class RegistryValidationError(ValueError):
    """Raised when the on-disk registry fails a structural check."""


def _is_bare_relative_local_path(value: str) -> bool:
    """True for a path with no URI scheme (https://, s3://, file://, ...)
    and not already an absolute filesystem path -- i.e. something that only
    makes sense relative to *some* root, as opposed to a self-contained
    location. Used to anchor registry-recorded relative paths (meant for
    portable local/test fixtures) against the directory they were loaded
    from, without touching real absolute or remote entries.
    """
    return not urlparse(value).scheme and not value.startswith("/")


class HatsRegistry:
    """Read-only, in-memory index over a `hats-registry`-style directory tree.

    Expected layout, rooted at `registry_root`::

        <core_id>/core.json
        <core_id>/extensions/<extension_id>.json

    Usage::

        registry = HatsRegistry.from_directory(Path("registry/"))
        registry.get_extensions("gaia_dr3")
    """

    def __init__(
        self,
        cores: dict[str, CoreCatalogEntry],
        extensions: dict[str, list[ExtensionCatalogEntry]],
    ) -> None:
        self._cores = cores
        self._extensions_by_core = extensions
        # Flat index for direct extension_id -> entry lookup.
        self._extensions_by_id: dict[str, ExtensionCatalogEntry] = {
            entry.catalog_id: entry
            for entries in extensions.values()
            for entry in entries
        }

    @classmethod
    def from_directory(cls, registry_root: Path, validate: bool = True) -> "HatsRegistry":
        """Build an index by scanning `<registry_root>/*/core.json` and
        `<registry_root>/*/extensions/*.json` on local disk.

        Prefer `load()` for normal use — this is intended for local
        development against a cloned/checked-out copy of the registry, or
        for offline use.

        Parameters
        ----------
        registry_root : Path
            Directory containing one subfolder per core catalog.
        validate : bool
            If True (default), enforce the structural invariants documented
            on `_build` — filename/catalog_id agreement and dangling
            `extends` references. Set False to load a possibly-inconsistent
            tree anyway (e.g. for a linter that wants to report all issues
            rather than stop at the first one).
        """
        core_files = {
            str(p.relative_to(registry_root)): p.read_text()
            for p in registry_root.glob("*/core.json")
        }
        ext_files = {
            str(p.relative_to(registry_root)): p.read_text()
            for p in registry_root.glob("*/extensions/*.json")
        }
        registry = cls._build(core_files, ext_files, validate=validate)
        registry._anchor_relative_paths(registry_root.parent)
        return registry

    @classmethod
    def load(
        cls,
        ref: Optional[str] = None,
        validate: bool = True,
        session: Optional[requests.Session] = None,
    ) -> "HatsRegistry":
        """Build an index by fetching registry JSON directly from GitHub.

        This is the normal entry point — it requires no bundled data and no
        local checkout, so a fresh `pip install hats-registry` sees exactly
        what's on GitHub at `ref`, rather than whatever was current when the
        package was released. Always reads from the `hats-registry` repo
        itself (astronomy-commons ecosystem) — there's no fork/mirror
        support in this prototype.

        Fetching happens in two possible ways:

        1. (Preferred) Read `registry/_index.json` — a manifest of every
           core/extension file path, regenerated by CI on each merge to
           `main` (see `scripts/build_index.py`) — via
           raw.githubusercontent.com. This is not subject to GitHub's low
           anonymous API rate limit, so it's safe as an unauthenticated
           default even from a shared IP (CI runner, office NAT, etc).
        2. (Fallback) If no index file is present — e.g. a `ref` predating
           the index's introduction — fall back to a live recursive tree
           listing via the GitHub REST API. This works but costs against
           the 60/hour anonymous rate limit (5000/hour if `session` carries
           a token).

        Parameters
        ----------
        ref : str, optional
            Branch, tag, or commit SHA to read from. Defaults to whatever
            `get_default_ref()` currently returns (`"main"` unless changed
            via `set_default_ref()`). Pass a tag or SHA explicitly for a
            reproducible snapshot, e.g. `HatsRegistry.load(ref="v0.3.0")`.
        validate : bool
            See `from_directory`.
        session : requests.Session, optional
            Reuse a session (e.g. one with auth headers to avoid GitHub's
            anonymous rate limit on the fallback path) instead of issuing
            unauthenticated requests.
        """
        resolved_ref = ref if ref is not None else get_default_ref()
        http = session or requests.Session()
        raw_base = f"https://raw.githubusercontent.com/{DEFAULT_OWNER}/{DEFAULT_REPO}/{resolved_ref}"

        def fetch_raw(relative_to_repo_root: str) -> str:
            resp = http.get(f"{raw_base}/{relative_to_repo_root}", timeout=30)
            resp.raise_for_status()
            return resp.text

        core_rel_paths, ext_rel_paths = cls._paths_from_index(http, raw_base)
        if core_rel_paths is None:
            core_rel_paths, ext_rel_paths = cls._paths_from_tree_api(
                http, resolved_ref
            )

        core_files = {
            rel: fetch_raw(f"{DEFAULT_REGISTRY_SUBDIR}/{rel}") for rel in core_rel_paths
        }
        ext_files = {
            rel: fetch_raw(f"{DEFAULT_REGISTRY_SUBDIR}/{rel}") for rel in ext_rel_paths
        }

        return cls._build(core_files, ext_files, validate=validate)

    @staticmethod
    def _paths_from_index(
        http: requests.Session, raw_base: str
    ) -> tuple[Optional[list[str]], Optional[list[str]]]:
        """Try reading the pre-built `_index.json` manifest. Returns
        `(None, None)` if it doesn't exist (404), signaling the caller to
        fall back to the live tree API. Any other HTTP error propagates.
        """
        index_url = f"{raw_base}/{DEFAULT_REGISTRY_SUBDIR}/{INDEX_FILENAME}"
        response = http.get(index_url, timeout=30)
        if response.status_code == 404:
            return None, None
        response.raise_for_status()
        index = response.json()
        return index.get("cores", []), index.get("extensions", [])

    @staticmethod
    def _paths_from_tree_api(
        http: requests.Session, ref: str
    ) -> tuple[list[str], list[str]]:
        """Fallback: list registry contents via a live recursive tree
        listing from the GitHub REST API. Subject to the (low, if
        unauthenticated) API rate limit — used only when no `_index.json`
        is available.
        """
        tree_url = (
            f"https://api.github.com/repos/{DEFAULT_OWNER}/{DEFAULT_REPO}/git/trees/{ref}"
            "?recursive=1"
        )
        response = http.get(tree_url, timeout=30)
        response.raise_for_status()
        tree = response.json()
        if tree.get("truncated"):
            raise RegistryValidationError(
                f"GitHub tree listing for {DEFAULT_OWNER}/{DEFAULT_REPO}@{ref} "
                "was truncated; registry is too large for a single recursive fetch."
            )

        prefix = f"{DEFAULT_REGISTRY_SUBDIR}/"
        core_rel_paths = []
        ext_rel_paths = []
        for node in tree.get("tree", []):
            path = node.get("path", "")
            if node.get("type") != "blob" or not path.startswith(prefix):
                continue
            relative = path[len(prefix):]
            if _CORE_PATH_RE.match(relative):
                core_rel_paths.append(relative)
            elif _EXTENSION_PATH_RE.match(relative):
                ext_rel_paths.append(relative)
        return core_rel_paths, ext_rel_paths

    @classmethod
    def _build(
        cls,
        core_files: dict[str, str],
        ext_files: dict[str, str],
        validate: bool,
    ) -> "HatsRegistry":
        """Shared parsing/validation core for both `from_directory` and
        `load`. Takes `{relative_path: raw_json_text}` mappings so the
        validation logic (filename/catalog_id agreement, dangling `extends`)
        is identical regardless of whether the data came from local disk or
        a GitHub fetch.
        """
        cores: dict[str, CoreCatalogEntry] = {}
        for rel_path, text in core_files.items():
            entry = _entry_adapter.validate_json(text)
            if not isinstance(entry, CoreCatalogEntry):
                raise RegistryValidationError(
                    f"{rel_path} does not declare catalog_type='core'"
                )
            match = _CORE_PATH_RE.match(rel_path)
            folder_name = match.group("core_id") if match else None
            if validate and entry.catalog_id != folder_name:
                raise RegistryValidationError(
                    f"{rel_path}: catalog_id '{entry.catalog_id}' does not "
                    f"match parent directory name '{folder_name}'"
                )
            cores[entry.catalog_id] = entry

        extensions: dict[str, list[ExtensionCatalogEntry]] = {}
        for rel_path, text in ext_files.items():
            entry = _entry_adapter.validate_json(text)
            if not isinstance(entry, ExtensionCatalogEntry):
                raise RegistryValidationError(
                    f"{rel_path} does not declare catalog_type='extension'"
                )
            match = _EXTENSION_PATH_RE.match(rel_path)
            expected_id = match.group("ext_id") if match else None
            if validate and entry.catalog_id != expected_id:
                raise RegistryValidationError(
                    f"{rel_path}: catalog_id '{entry.catalog_id}' does not "
                    f"match filename '{expected_id}'"
                )
            if validate and entry.extends not in cores:
                raise RegistryValidationError(
                    f"{rel_path}: extends unknown core catalog "
                    f"'{entry.extends}' (no {entry.extends}/core.json)"
                )
            if validate and entry.extends in cores:
                core_mirrors = set(cores[entry.extends].paths)
                unknown_mirrors = set(entry.paths) - core_mirrors - {PRIMARY_MIRROR}
                if unknown_mirrors:
                    raise RegistryValidationError(
                        f"{rel_path}: paths declares mirror(s) "
                        f"{sorted(unknown_mirrors)} not present on its core "
                        f"catalog '{entry.extends}' (which has mirrors "
                        f"{sorted(core_mirrors)}) -- a co-located copy can't "
                        "exist at a mirror the core catalog doesn't have"
                    )
            extensions.setdefault(entry.extends, []).append(entry)

        return cls(cores, extensions)

    def _anchor_relative_paths(self, anchor: Path) -> None:
        """For a locally-loaded registry, resolve any bare relative path in
        every entry's `paths` dict against `anchor` (the directory the
        registry directory itself lives in), so they become unambiguous
        absolute paths -- comparable against a catalog's actual opened
        location regardless of the caller's own working directory.

        Real absolute paths and URIs with an explicit scheme (https://,
        s3://, file://, ...) are left untouched; only used by
        `from_directory` -- `load()`'s registry data always comes from
        GitHub, where every recorded path is already a real absolute URI.
        """
        all_entries: list[CatalogEntryBase] = [
            *self._cores.values(),
            *self._extensions_by_id.values(),
        ]
        for entry in all_entries:
            resolved = {
                label: (
                    str((anchor / value).resolve())
                    if _is_bare_relative_local_path(value)
                    else value
                )
                for label, value in entry.paths.items()
            }
            entry.paths = resolved

    def get_core(self, catalog_id: str) -> Optional[CoreCatalogEntry]:
        """Look up a core catalog entry by ID."""
        return self._cores.get(catalog_id)

    def get_extension(self, catalog_id: str) -> Optional[ExtensionCatalogEntry]:
        """Look up a single extension entry by its own ID."""
        return self._extensions_by_id.get(catalog_id)

    def get_extensions(self, core_id: str) -> list[ExtensionCatalogEntry]:
        """Return all extensions registered against a given core catalog."""
        return list(self._extensions_by_core.get(core_id, []))

    def resolve(self, catalog_id: str) -> Union[CoreCatalogEntry, ExtensionCatalogEntry]:
        """Look up any entry — core or extension — by its catalog_id."""
        if catalog_id in self._cores:
            return self._cores[catalog_id]
        if catalog_id in self._extensions_by_id:
            return self._extensions_by_id[catalog_id]
        raise KeyError(f"No registry entry found for catalog_id '{catalog_id}'")


def json_schema() -> dict:
    """Return the JSON Schema for a registry entry, for use in CI validation
    or editor tooling. Generated from the Pydantic models so it can never
    drift from what `HatsRegistry` actually accepts.
    """
    return _entry_adapter.json_schema()