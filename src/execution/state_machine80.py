from __future__ import annotations

from dataclasses import dataclass

from src.execution.live.models import CommandStatus, TERMINAL_STATUSES


@dataclass(frozen=True)
class TransitionDecision80:
    current: str
    target: str
    apply: bool
    duplicate: bool = False
    stale: bool = False
    reason: str = ""


_ALLOWED = {
    CommandStatus.PENDING.value: {
        CommandStatus.CLAIMED.value,
        CommandStatus.ACKNOWLEDGED.value,
        CommandStatus.WORKING.value,
        CommandStatus.REJECTED.value,
        CommandStatus.CANCELLED.value,
        CommandStatus.EXPIRED.value,
    },
    CommandStatus.CLAIMED.value: {
        CommandStatus.ACKNOWLEDGED.value,
        CommandStatus.WORKING.value,
        CommandStatus.PARTIAL.value,
        CommandStatus.FILLED.value,
        CommandStatus.REJECTED.value,
        CommandStatus.CANCELLED.value,
        CommandStatus.EXPIRED.value,
    },
    CommandStatus.ACKNOWLEDGED.value: {
        CommandStatus.WORKING.value,
        CommandStatus.PARTIAL.value,
        CommandStatus.FILLED.value,
        CommandStatus.REJECTED.value,
        CommandStatus.CANCELLED.value,
    },
    CommandStatus.WORKING.value: {
        CommandStatus.PARTIAL.value,
        CommandStatus.FILLED.value,
        CommandStatus.REJECTED.value,
        CommandStatus.CANCELLED.value,
    },
    CommandStatus.PARTIAL.value: {
        CommandStatus.PARTIAL.value,
        CommandStatus.FILLED.value,
        CommandStatus.REJECTED.value,
        CommandStatus.CANCELLED.value,
    },
    CommandStatus.FILLED.value: {
        CommandStatus.CLOSED.value,
        CommandStatus.REJECTED.value,
    },
}

_PROGRESS = {
    CommandStatus.PENDING.value: 0,
    CommandStatus.CLAIMED.value: 1,
    CommandStatus.ACKNOWLEDGED.value: 2,
    CommandStatus.WORKING.value: 3,
    CommandStatus.PARTIAL.value: 4,
    CommandStatus.FILLED.value: 5,
    CommandStatus.CLOSED.value: 6,
    CommandStatus.CANCELLED.value: 6,
    CommandStatus.REJECTED.value: 6,
    CommandStatus.EXPIRED.value: 6,
}


def resolve_transition80(current: str, target: str) -> TransitionDecision80:
    current = str(current or "").upper()
    target = str(target or "").upper()
    if not current or not target:
        return TransitionDecision80(current, target, False, reason="Missing execution state.")
    if current == target:
        # A new PARTIAL/WORKING callback can carry useful fill/order metadata even
        # when the state name itself does not change. Exact duplicate event IDs
        # are filtered before this function is called.
        return TransitionDecision80(
            current,
            target,
            True,
            duplicate=True,
            reason="Idempotent same-state broker update accepted without regression.",
        )
    if current in TERMINAL_STATUSES:
        return TransitionDecision80(
            current,
            target,
            False,
            stale=True,
            reason=f"Terminal command state {current} cannot regress to {target}.",
        )
    if target in _ALLOWED.get(current, set()):
        return TransitionDecision80(current, target, True, reason=f"Legal execution transition {current}->{target}.")
    if _PROGRESS.get(target, -1) <= _PROGRESS.get(current, -1):
        return TransitionDecision80(
            current,
            target,
            False,
            stale=True,
            reason=f"Stale/out-of-order execution transition {current}->{target} ignored.",
        )
    return TransitionDecision80(
        current,
        target,
        False,
        reason=f"Illegal execution transition {current}->{target}; state preserved.",
    )
