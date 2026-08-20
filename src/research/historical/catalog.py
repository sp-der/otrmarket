from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import re


@dataclass(frozen=True)
class InstrumentSpec:
    instrument: str
    root: str
    size_class: str
    tick_size: float
    point_value: float

    @property
    def tick_value(self) -> float:
        return self.tick_size * self.point_value


CONTRACT_SPECS = {
    "NQ": InstrumentSpec("NQ", "NQ", "MINI", 0.25, 20.0),
    "MNQ": InstrumentSpec("MNQ", "NQ", "MICRO", 0.25, 2.0),
    "ES": InstrumentSpec("ES", "ES", "MINI", 0.25, 50.0),
    "MES": InstrumentSpec("MES", "ES", "MICRO", 0.25, 5.0),
    "GC": InstrumentSpec("GC", "GC", "MINI", 0.10, 100.0),
    "MGC": InstrumentSpec("MGC", "GC", "MICRO", 0.10, 10.0),
}

MONTH_CODES = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}


def contract_spec(instrument: str) -> InstrumentSpec:
    key = instrument.strip().upper()
    if key not in CONTRACT_SPECS:
        raise ValueError(f"Unsupported OTR futures instrument: {instrument}")
    return CONTRACT_SPECS[key]


def parse_contract(contract: str) -> tuple[InstrumentSpec, date | None]:
    """Return exact instrument metadata and an approximate contract expiry.

    Exchange-specific last-trade dates belong in an authoritative rollover
    manifest. Until supplied, month-name contracts use the final calendar day
    and are explicitly marked ESTIMATED by the catalog writer.
    """
    value = " ".join((contract or "").strip().upper().split())
    match = re.fullmatch(r"(MNQ|NQ|MES|ES|MGC|GC)(?:\s+([A-Z]{3})(\d{2,4}))?", value)
    if not match:
        raise ValueError(f"Unsupported or unparseable futures contract: {contract}")
    spec = contract_spec(match.group(1))
    if not match.group(2):
        return spec, None
    month = MONTH_CODES.get(match.group(2))
    if month is None:
        raise ValueError(f"Unknown contract month: {match.group(2)}")
    raw_year = int(match.group(3))
    year = raw_year + 2000 if raw_year < 100 else raw_year
    if month == 12:
        following = date(year + 1, 1, 1)
    else:
        following = date(year, month + 1, 1)
    from datetime import timedelta
    return spec, following - timedelta(days=1)
