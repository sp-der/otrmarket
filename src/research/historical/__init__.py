"""Immutable historical futures data foundation for OTR research."""

from .catalog import CONTRACT_SPECS, contract_spec, parse_contract
from .store import HistoricalStore, RawEvent
from .acquisition import ImportMetadata, ImportValidationError, import_ninjatrader

__all__ = ["CONTRACT_SPECS", "HistoricalStore", "RawEvent", "ImportMetadata",
           "ImportValidationError", "import_ninjatrader", "contract_spec", "parse_contract"]
