from __future__ import annotations

import uuid

from src.storage.database import get_engine_state, set_engine_state

# Durable per-run identity for Operation 8.1 research. Minted once and kept in
# engine_state so every strategy_setups/paper_trades row can be scoped to the
# run that produced it instead of Research Lab silently pooling trades across
# generations (7.x, 8.0, 8.1, older replay runs).
RUN_ID_STATE_KEY = "operation81_research_run_id"
ENGINE_VERSION = "src.main_81"
OPERATION_VERSION = "Operation 8.1"


def _mint_run_id() -> str:
    return f"run-81-{uuid.uuid4().hex[:12]}"


def current_run_id(connection) -> str:
    """Return the durable run identifier for the active evaluation/replay run.

    Persisted in engine_state so it survives process restarts and routine
    redeploys. It only changes when rotate_run_id() is called alongside an
    explicit, already-authorized data reset (see
    src/dashboard/server_81.py:_reset_active_replay_progress_81) -- never on
    a plain restart.
    """
    existing = (get_engine_state(connection, RUN_ID_STATE_KEY, "") or "").strip()
    if existing:
        return existing
    minted = _mint_run_id()
    set_engine_state(connection, RUN_ID_STATE_KEY, minted)
    return minted


def rotate_run_id(connection) -> str:
    """Mint and persist a new run id. Does not itself delete or touch any data;

    callers are responsible for only invoking this alongside an already
    authorized, explicit reset so the new id genuinely marks a fresh run.
    """
    minted = _mint_run_id()
    set_engine_state(connection, RUN_ID_STATE_KEY, minted)
    return minted
