from ._version import __version__
from .registry import HatsRegistry, CoreCatalogEntry, ExtensionCatalogEntry
from .scripts.build_index import build_index

__all__ = ["__version__"]
