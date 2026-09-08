from __future__ import annotations

from dataclasses import dataclass, field
from math import isclose


@dataclass(frozen=True)
class ExecutionSnapshot:
    """Normalized execution state used to compare OTR and Nautilus results."""

    setup_id: str
    status: str
    entry_price: float | None = None
    exit_price: float | None = None
    result: str | None = None
    result_r: float | None = None
    result_dollars: float | None = None


@dataclass(frozen=True)
class ParityResult:
    setup_id: str
    matched: bool
    differences: tuple[str, ...] = field(default_factory=tuple)


def _float_equal(left: float | None, right: float | None, *, tolerance: float) -> bool:
    if left is None or right is None:
        return left is right
    return isclose(float(left), float(right), rel_tol=0.0, abs_tol=tolerance)


def compare_execution_snapshots(
    otr: ExecutionSnapshot,
    nautilus: ExecutionSnapshot,
    *,
    price_tolerance: float = 1e-9,
    pnl_tolerance: float = 0.01,
) -> ParityResult:
    """Compare two normalized execution snapshots and report exact divergence points."""
    differences: list[str] = []

    if otr.setup_id != nautilus.setup_id:
        differences.append(f"setup_id:{otr.setup_id}!={nautilus.setup_id}")
    if str(otr.status).upper() != str(nautilus.status).upper():
        differences.append(f"status:{otr.status}!={nautilus.status}")
    if not _float_equal(otr.entry_price, nautilus.entry_price, tolerance=price_tolerance):
        differences.append(f"entry_price:{otr.entry_price}!={nautilus.entry_price}")
    if not _float_equal(otr.exit_price, nautilus.exit_price, tolerance=price_tolerance):
        differences.append(f"exit_price:{otr.exit_price}!={nautilus.exit_price}")
    if (otr.result or "").upper() != (nautilus.result or "").upper():
        differences.append(f"result:{otr.result}!={nautilus.result}")
    if not _float_equal(otr.result_r, nautilus.result_r, tolerance=price_tolerance):
        differences.append(f"result_r:{otr.result_r}!={nautilus.result_r}")
    if not _float_equal(otr.result_dollars, nautilus.result_dollars, tolerance=pnl_tolerance):
        differences.append(f"result_dollars:{otr.result_dollars}!={nautilus.result_dollars}")

    return ParityResult(
        setup_id=otr.setup_id,
        matched=not differences,
        differences=tuple(differences),
    )
