from __future__ import annotations

import json

from src.dashboard import server_72o as base
from src.storage.database import get_connection


LOSS_IDS = {
    "01d9f35c02ec",
    "0b50d1f07916",
    "5e209cb2aefa",
    "5a3eca46344c",
    "839cb610a77b",
    "bafab08918ae",
    "3093dfad6621",
    "1c0120a58bbe",
    "4d32daf2b28f",
}

WIN_IDS = {
    "69fa67bb40d0",
    "74821715bf6e",
    "0fb23f9d160a",
    "7b1b6cd54c66",
    "f191ead59424",
}


def _audit_context(label: str, setup_ids: set[str]) -> None:
    connection = get_connection()
    try:
        rows = connection.execute(
            """
            SELECT s.setup_id, s.symbol, s.timeframe, s.direction, s.created_at,
                   s.trigger_type, s.risk_reward, s.payload_json,
                   p.result, p.result_r, p.risk_dollars, p.result_dollars,
                   p.opened_at, p.closed_at
            FROM strategy_setups s
            LEFT JOIN paper_trades p ON p.setup_id = s.setup_id
            WHERE s.setup_id IN ({})
            ORDER BY s.created_at ASC
            """.format(",".join("?" for _ in setup_ids)),
            tuple(sorted(setup_ids)),
        ).fetchall()
        print(f"{label} CONTEXT AUDIT 7.2 rows={len(rows)}", flush=True)
        for row in rows:
            try:
                payload = json.loads(row[7] or "{}")
            except Exception:
                payload = {}
            metadata = payload.get("metadata", {}) or {}
            ctx = metadata.get("a_plus_context", {}) or {}
            session = metadata.get("session_consistency", {}) or {}
            operating = metadata.get("operating_mode", {}) or {}
            early = metadata.get("early_entry_72h", {}) or metadata.get("early_entry", {}) or {}
            print(
                f"{label} CONTEXT 7.2 " + repr({
                    "setup_id": row[0], "tf": row[2], "dir": row[3], "created_at": row[4],
                    "trigger": row[5], "rr": row[6], "result": row[8], "result_r": row[9],
                    "risk_dollars": row[10], "result_dollars": row[11], "opened_at": row[12], "closed_at": row[13],
                    "strategy": metadata.get("strategy"), "entry_type": metadata.get("entry_type"),
                    "session_tier": metadata.get("session_tier") or session.get("session_tier"),
                    "local_time": session.get("local_time"), "quality_grade": ctx.get("quality_grade"),
                    "quality_score": ctx.get("quality_score"), "context_tf": ctx.get("context_timeframe"),
                    "context_bias": ctx.get("higher_timeframe_bias"), "narrative_tf": ctx.get("narrative_timeframe"),
                    "narrative_bias": ctx.get("narrative_bias"), "narrative_conflict": ctx.get("narrative_conflict"),
                    "disp_body": ctx.get("displacement_body_ratio"), "disp_range": ctx.get("displacement_range_ratio"),
                    "fvg_age": ctx.get("entry_fvg_age_bars"), "mi_score": ctx.get("market_intelligence_score"),
                    "execution_tier": metadata.get("execution_tier"), "operating_mode": operating.get("mode"), "early": early,
                }),
                flush=True,
            )
    finally:
        connection.close()


def main() -> None:
    _audit_context("LOSS", LOSS_IDS)
    _audit_context("WIN", WIN_IDS)
    base.main()


if __name__ == "__main__":
    main()
