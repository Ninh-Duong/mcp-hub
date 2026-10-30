"""Optional activity UI extension: pixel barn, daily activity, and connected AI clients.

Installed only by `service.py` when the Hub starts with `hubctl ... -Ui`; core modules never import it.
"""
from .extension import ActivityUi, install

__all__ = ["ActivityUi", "install"]
