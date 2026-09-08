"""Optional NautilusTrader shadow execution integration.

This package must never become the live execution authority unless explicitly
promoted after replay parity testing.
"""

from .config import NautilusShadowConfig
from .parity import ExecutionSnapshot, ParityResult, compare_execution_snapshots

__all__ = [
    "NautilusShadowConfig",
    "ExecutionSnapshot",
    "ParityResult",
    "compare_execution_snapshots",
]
