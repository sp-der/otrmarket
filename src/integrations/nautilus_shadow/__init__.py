"""Optional NautilusTrader shadow execution integration.

This package must never become the live execution authority unless explicitly
promoted after replay parity testing.
"""

from .bridge import NautilusShadowBridge
from .config import NautilusShadowConfig
from .execution_parity import NautilusBracketResult, ShadowBracketIntent, simulate_gold_bracket
from .normalize import from_paper_position
from .parity import ExecutionSnapshot, ParityResult, compare_execution_snapshots
from .probe import probe_nautilus

__all__ = [
    "NautilusShadowBridge",
    "NautilusShadowConfig",
    "NautilusBracketResult",
    "ShadowBracketIntent",
    "ExecutionSnapshot",
    "ParityResult",
    "compare_execution_snapshots",
    "from_paper_position",
    "probe_nautilus",
    "simulate_gold_bracket",
]
