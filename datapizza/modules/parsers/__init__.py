from pkgutil import extend_path

__path__ = extend_path(__path__, __name__)

from .text_parser import TextParser

__all__ = ["TextParser"]
