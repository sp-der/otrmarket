from __future__ import annotations

import copy
import json
from typing import Any


RESEARCH_KEYS = {
    "classification",
    "summary",
    "evidence",
    "hypotheses",
    "experiments",
    "evidence_gaps",
    "confidence",
    "promotion_recommendation",
}


def _json_object_from_text(value: str) -> dict[str, Any] | None:
    text = str(value or "").strip()
    if not text:
        return None

    candidates = [text]
    if "```" in text:
        for block in text.split("```"):
            cleaned = block.strip()
            if cleaned.lower().startswith("json"):
                cleaned = cleaned[4:].lstrip()
            if cleaned.startswith("{") and cleaned.endswith("}"):
                candidates.append(cleaned)

    first = text.find("{")
    last = text.rfind("}")
    if first >= 0 and last > first:
        candidates.append(text[first : last + 1])

    for candidate in candidates:
        try:
            loaded = json.loads(candidate)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if isinstance(loaded, dict):
            return loaded
    return None


def extract_research_result(value: Any) -> dict[str, Any] | None:
    """Extract the substantive Vibe answer from its script-friendly CLI envelope."""
    if isinstance(value, str):
        parsed = _json_object_from_text(value)
        if parsed is None:
            return None
        return extract_research_result(parsed)

    if not isinstance(value, dict):
        return None

    if RESEARCH_KEYS.intersection(value):
        return value

    for key in ("content", "answer", "result", "output", "final", "message"):
        child = value.get(key)
        if isinstance(child, dict):
            extracted = extract_research_result(child)
            if extracted is not None:
                return extracted
        elif isinstance(child, str):
            parsed = _json_object_from_text(child)
            if parsed is not None:
                extracted = extract_research_result(parsed)
                if extracted is not None:
                    return extracted

    return None


def normalize_vibe_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Expose research content while preserving Vibe run metadata for diagnostics."""
    data = copy.deepcopy(snapshot)
    normalized = []
    for finding in data.get("recent_findings", []) or []:
        item = dict(finding)
        transport = item.get("result")
        extracted = extract_research_result(transport)
        if extracted is not None:
            item["transport_result"] = transport
            item["result"] = extracted
            item["analysis_extracted"] = True
        else:
            item["analysis_extracted"] = False
        normalized.append(item)
    data["recent_findings"] = normalized
    return data
