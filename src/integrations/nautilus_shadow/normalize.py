from __future__ import annotations

from typing import Any

from .parity import ExecutionSnapshot


def from_paper_position(position: Any) -> ExecutionSnapshot:
    """Normalize an OTR PaperPosition-like object without importing execution internals."""
    setup = position.setup
    entry_price = getattr(setup, "entry_price", None)
    if getattr(position, "opened_at", None) is None and str(getattr(position, "status", "")).upper() == "PENDING":
        # Pending setups have a requested limit, not a fill.
        normalized_entry = None
    else:
        normalized_entry = entry_price

    return ExecutionSnapshot(
        setup_id=str(setup.setup_id),
        status=str(getattr(position, "status", "UNKNOWN")),
        entry_price=float(normalized_entry) if normalized_entry is not None else None,
        exit_price=(
            float(position.exit_price)
            if getattr(position, "exit_price", None) is not None
            else None
        ),
        result=getattr(position, "result", None),
        result_r=(
            float(position.result_r)
            if getattr(position, "result_r", None) is not None
            else None
        ),
        result_dollars=(
            float(position.result_dollars)
            if getattr(position, "result_dollars", None) is not None
            else None
        ),
    )
