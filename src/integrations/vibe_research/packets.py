from __future__ import annotations

import json
import sqlite3
from typing import Any

from src.research.lab_v01 import (
    closed_gold_trades,
    counterfactual_candidates,
    evidence_metrics,
)


def _hypotheses(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    try:
        rows = connection.execute(
            """
            SELECT hypothesis_id,title,question,metric,status,notes
            FROM research_hypotheses_v01
            ORDER BY hypothesis_id
            """
        ).fetchall()
    except sqlite3.Error:
        return []
    return [
        {
            "hypothesis_id": row[0],
            "title": row[1],
            "question": row[2],
            "metric": row[3],
            "status": row[4],
            "notes": row[5] or "",
        }
        for row in rows
    ]


def build_vibe_packet(connection: sqlite3.Connection, setup_id: str) -> dict[str, Any]:
    """Project persisted OTR evidence into a read-only research packet."""
    trades = closed_gold_trades(connection, limit=500)
    target = next((trade for trade in trades if trade["setup_id"] == str(setup_id)), None)
    if target is None:
        raise ValueError(f"Closed Gold trade {setup_id} is not available for research projection")

    recent = trades[:20]
    return {
        "contract": {
            "authority": "NON_AUTHORITATIVE_RESEARCH_ONLY",
            "strategy_mutation_allowed": False,
            "risk_mutation_allowed": False,
            "broker_access_allowed": False,
            "source": "OTR Market Operation 8.1",
        },
        "target_trade": target,
        "baseline": evidence_metrics(trades),
        "recent_trade_metrics": evidence_metrics(recent),
        "counterfactual_geometry": counterfactual_candidates(target),
        "open_hypotheses": _hypotheses(connection),
        "recent_trades": [
            {
                "setup_id": trade["setup_id"],
                "closed_at": trade["closed_at"],
                "timeframe": trade["timeframe"],
                "direction": trade["direction"],
                "grade": trade["grade"],
                "regime": trade["regime"],
                "setup_family": trade["setup_family"],
                "result": trade["result"],
                "result_r": trade["result_r"],
                "result_dollars": trade["result_dollars"],
                "execution_certification": trade["execution_certification"],
                "nautilus": trade["nautilus"],
            }
            for trade in recent
        ],
    }


def research_prompt(packet: dict[str, Any]) -> str:
    """Create a bounded retrospective prompt for the isolated Vibe agent."""
    payload = json.dumps(packet, sort_keys=True, separators=(",", ":"), default=str)
    return f"""You are the non-authoritative research scientist for OTR Market.

Analyze the supplied CLOSED Gold replay trade and surrounding evidence. OTR Operation 8.1 remains the only strategy authority. Nautilus is the execution referee. You are not allowed to place orders, change risk, modify strategy code, connect to a broker, or issue live trading instructions.

Your job is retrospective research only:
1. Separate MARKET/STRATEGY/EXECUTION/DATA/RISK_POLICY causes where evidence supports them.
2. Treat small samples as insufficient evidence instead of overfitting.
3. Use Nautilus certification when present to distinguish execution from strategy behavior.
4. Review the supplied counterfactuals as GEOMETRY ONLY. Do not claim an alternate entry would have won unless outcome evidence exists.
5. Propose at most three falsifiable hypotheses and a replay experiment for each.
6. Identify evidence gaps.
7. Do not recommend changing production from one trade.

Return a concise machine-readable JSON object with these keys:
classification, summary, evidence, hypotheses, experiments, evidence_gaps, confidence, promotion_recommendation.
`promotion_recommendation` must be one of HOLD, COLLECT_MORE, TEST_IN_REPLAY. Never return a production/live promotion recommendation.

OTR_RESEARCH_PACKET={payload}
"""
