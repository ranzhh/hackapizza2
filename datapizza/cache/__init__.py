# Import MemoryCache from core implementation
from pkgutil import extend_path

__path__ = extend_path(__path__, __name__)

from datapizza.core.cache import MemoryCache

__all__ = ["MemoryCache"]
