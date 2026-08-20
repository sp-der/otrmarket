from __future__ import annotations

from src.research.historical.catalog import contract_spec


ROOT_MICRO = {"NQ": "MNQ", "ES": "MES", "GC": "MGC"}


def micro_spec(root_symbol: str):
    try:
        return contract_spec(ROOT_MICRO[root_symbol.upper()])
    except KeyError as exc:
        raise ValueError(f"Unsupported execution root: {root_symbol}") from exc


def execution_contract(root_symbol: str, signal_contract: str | None = None) -> str:
    instrument = ROOT_MICRO[root_symbol.upper()]
    parts = (signal_contract or "").upper().split(maxsplit=1)
    return f"{instrument} {parts[1]}" if len(parts) == 2 else instrument
