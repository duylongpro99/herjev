"""Log sources. Importing this package registers the built-in types."""

from .base import Source
from . import command, file, http_poll, listen, ssh  # noqa: F401  (registration side effects)

__all__ = ["Source"]
