from ._version import __version__
from .registry import HatsRegistry, CoreCatalogEntry, ExtensionCatalogEntry, get_default_ref, set_default_ref
from .scripts.build_index import build_index

__all__ = ["__version__"]
