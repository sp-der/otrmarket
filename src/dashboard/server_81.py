from __future__ import annotations

from src.dashboard import server_80 as base


def _promote_engine_81() -> str:
    base.core72.promoted_engine_module = lambda requested=None: "src.main_81"
    return base.core72.promoted_engine_module()


def main() -> None:
    # server_80 still owns the proven dashboard/API/UI setup. Replace only its
    # engine promotion hook so the same supervisor launches Operation 8.1.
    base._promote_engine_80 = _promote_engine_81
    print(
        "Operation 8.1 supervisor: Operation 8.0 dashboard + Gold Execution Conversion engine; "
        "first-touch zones, registration-time entry life, dynamic R:R and $750/$500 eval sizing enabled.",
        flush=True,
    )
    base.main()


if __name__ == "__main__":
    main()
