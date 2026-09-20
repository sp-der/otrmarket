"""Operation 8.2 Confluence Intelligence -- SHADOW RESEARCH ONLY.

This package never places, blocks, resizes, approves, or rejects a real or
paper OTR trade. Operation 8.1 (src.main_81) remains the sole authoritative
trading engine. Every function here either reads already-computed OTR
evidence or writes to additive, OTR-8.2-owned tables; nothing here is ever
imported by an execution decision path.
"""

from __future__ import annotations

SHADOW_ONLY = True
OPERATION_VERSION_82 = "Operation 8.2"
