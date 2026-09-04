"""OTR Market 8.0 orchestration package.

Operation 8.0 deliberately keeps the proven 7.2T detectors and safety gates as
its reference implementation while moving orchestration into explicit, testable
components. New intelligence may rank candidates; it may never bypass a legacy
safety/quality rejection.
"""

from .models import CandidateAssessment80, DecisionTrace80, RegimeSnapshot80, TradePlan80

__all__ = [
    "CandidateAssessment80",
    "DecisionTrace80",
    "RegimeSnapshot80",
    "TradePlan80",
]
