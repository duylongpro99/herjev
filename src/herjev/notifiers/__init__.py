"""Notifiers: deliver alerts to users through channels."""

from .base import Notifier
from . import simple, teams  # noqa: F401  (registration side effects)

__all__ = ["Notifier"]
