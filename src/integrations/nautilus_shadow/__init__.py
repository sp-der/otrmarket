"""Optional NautilusTrader shadow execution integration.

This package must never become the live execution authority unless explicitly
promoted after replay parity testing.
"""

from .bridge import NautilusShadowBridge
from .config import NautilusShadowConfig
from .execution_parity import NautilusBracketResult, ShadowBracketIntent, simulate_gold_bracket
from .ledger import (
    GoldTradeCandidate,
    ParityLedgerRecord,
    ensure_parity_ledger,
    run_recent_gold_parity,
)
from .normalize import from_paper_position
from .parity import ExecutionSnapshot, ParityResult, compare_execution_snapshots
from .probe import probe_nautilus

__all__ = [
    "NautilusShadowBridge",
    "NautilusShadowConfig",
    "NautilusBracketResult",
    "ShadowBracketIntent",
    "GoldTradeCandidate",
    "ParityLedgerRecord",
    "ExecutionSnapshot",
    "ParityResult",
    "compare_execution_snapshots",
    "ensure_parity_ledger",
    "from_paper_position",
    "probe_nautilus",
    "run_recent_gold_parity",
    "simulate_gold_bracket",
]
