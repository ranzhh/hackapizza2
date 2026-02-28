from pkgutil import extend_path

__path__ = extend_path(__path__, __name__)

from .embedders import ChunkEmbedder, ClientEmbedder

__all__ = ["ChunkEmbedder", "ClientEmbedder"]
