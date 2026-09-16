from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.integrations.vibe_research.config import (
    VIBE_VERSION,
    VibeResearchConfig,
    load_vibe_research_config,
    safe_vibe_environment,
)
from src.integrations.vibe_research.packets import build_vibe_packet, research_prompt
from src.storage.database import get_connection


_WORKER_NAME = "otr-vibe-research-sidecar"
_worker_lock = threading.Lock()
_worker_stop = threading.Event()
_worker_thread: threading.Thread | None = None


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def ensure_vibe_research_schema(connection: sqlite3.Connection) -> None:
    """Create additive sidecar state only. OTR trading tables are never altered."""
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS vibe_research_jobs_v02 (
            setup_id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            queued_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            completed_at TEXT,
            provider TEXT NOT NULL DEFAULT '',
            model TEXT NOT NULL DEFAULT '',
            packet_path TEXT NOT NULL DEFAULT '',
            output_path TEXT NOT NULL DEFAULT '',
            last_error TEXT NOT NULL DEFAULT ''
        );

        CREATE INDEX IF NOT EXISTS idx_vibe_research_jobs_v02_status
        ON vibe_research_jobs_v02(status, updated_at DESC);

        CREATE TABLE IF NOT EXISTS vibe_research_findings_v02 (
            setup_id TEXT PRIMARY KEY,
            captured_at TEXT NOT NULL,
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            package_version TEXT NOT NULL,
            result_json TEXT NOT NULL,
            raw_output TEXT NOT NULL,
            authority TEXT NOT NULL DEFAULT 'NON_AUTHORITATIVE_RESEARCH_ONLY'
        );
        """
    )
    connection.commit()


def recover_interrupted_vibe_jobs(connection: sqlite3.Connection) -> int:
    ensure_vibe_research_schema(connection)
    now = _utc_iso()
    cursor = connection.execute(
        """
        UPDATE vibe_research_jobs_v02
        SET status='RETRY', updated_at=?, completed_at=NULL,
            last_error='Previous Vibe research attempt was interrupted by process restart.'
        WHERE status IN ('CLAIMED','RUNNING')
        """,
        (now,),
    )
    connection.commit()
    return max(0, int(cursor.rowcount or 0))


def _terminal_nautilus_status_clause() -> str:
    return "('CERTIFIED','UNAVAILABLE_RETENTION','ERROR')"


def _claim_next(connection: sqlite3.Connection, config: VibeResearchConfig) -> str | None:
    ensure_vibe_research_schema(connection)
    active_since = str(os.getenv("OTR_VIBE_ACTIVE_SINCE") or "").strip()
    # Wait until Nautilus has either certified the trade or reached a terminal
    # diagnostic state. ACTIVE_SINCE is a replay-market timestamp, not a wall-
    # clock queue timestamp, so every eligibility check joins back to the trade.
    row = connection.execute(
        f"""
        SELECT p.setup_id
        FROM paper_trades p
        LEFT JOIN nautilus_shadow_parity n ON n.setup_id=p.setup_id
        LEFT JOIN nautilus_shadow_auto_jobs nj ON nj.setup_id=p.setup_id
        LEFT JOIN vibe_research_jobs_v02 v ON v.setup_id=p.setup_id
        WHERE p.symbol='GC'
          AND p.status='CLOSED'
          AND p.opened_at IS NOT NULL
          AND p.closed_at IS NOT NULL
          AND v.setup_id IS NULL
          AND (?='' OR COALESCE(p.closed_at,p.updated_at) >= ?)
          AND (n.setup_id IS NOT NULL OR nj.status IN {_terminal_nautilus_status_clause()})
        ORDER BY COALESCE(p.closed_at,p.updated_at) ASC
        LIMIT 1
        """,
        (active_since, active_since),
    ).fetchone()

    if row is None and config.provider_ready and config.package_installed:
        # RETRY jobs are eligible by the replay trade timestamp. Using queued_at
        # here would mix historical packets with a fresh replay because both are
        # queued by the current wall clock.
        row = connection.execute(
            """
            SELECT v.setup_id
            FROM vibe_research_jobs_v02 v
            JOIN paper_trades p ON p.setup_id=v.setup_id
            WHERE v.status='RETRY'
              AND (?='' OR COALESCE(p.closed_at,p.updated_at) >= ?)
            ORDER BY COALESCE(p.closed_at,p.updated_at) DESC
            LIMIT 1
            """,
            (active_since, active_since),
        ).fetchone()

        # Provider/package-waiting jobs from the ACTIVE replay should resume
        # automatically once the provider is ready. Historical waiting packets
        # remain dormant unless the operator explicitly enables backfill.
        if row is None and active_since:
            row = connection.execute(
                """
                SELECT v.setup_id
                FROM vibe_research_jobs_v02 v
                JOIN paper_trades p ON p.setup_id=v.setup_id
                WHERE v.status IN ('WAITING_PROVIDER','PACKAGE_UNAVAILABLE')
                  AND COALESCE(p.closed_at,p.updated_at) >= ?
                ORDER BY COALESCE(p.closed_at,p.updated_at) DESC
                LIMIT 1
                """,
                (active_since,),
            ).fetchone()

        if row is None and _truthy(os.getenv("OTR_VIBE_BACKFILL_WAITING")):
            row = connection.execute(
                """
                SELECT setup_id FROM vibe_research_jobs_v02
                WHERE status IN ('WAITING_PROVIDER','PACKAGE_UNAVAILABLE')
                ORDER BY queued_at ASC
                LIMIT 1
                """
            ).fetchone()
    if row is None:
        return None

    setup_id = str(row[0])
    existing = connection.execute(
        "SELECT attempts,queued_at FROM vibe_research_jobs_v02 WHERE setup_id=?",
        (setup_id,),
    ).fetchone()
    now = _utc_iso()
    attempts = int(existing[0] or 0) + 1 if existing else 1
    queued_at = str(existing[1]) if existing else now
    connection.execute(
        """
        INSERT INTO vibe_research_jobs_v02(
            setup_id,status,attempts,queued_at,updated_at,completed_at,provider,model,
            packet_path,output_path,last_error
        ) VALUES (?, 'CLAIMED', ?, ?, ?, NULL, ?, ?, '', '', '')
        ON CONFLICT(setup_id) DO UPDATE SET
            status='CLAIMED', attempts=excluded.attempts, updated_at=excluded.updated_at,
            completed_at=NULL, provider=excluded.provider, model=excluded.model,
            last_error=''
        """,
        (setup_id, attempts, queued_at, now, config.provider, config.model),
    )
    connection.commit()
    return setup_id


def _write_packet(config: VibeResearchConfig, setup_id: str, packet: dict[str, Any]) -> tuple[Path, Path]:
    packets = config.workdir / "packets"
    outputs = config.workdir / "outputs"
    prompts = config.workdir / "prompts"
    home = config.workdir / "home"
    for folder in (packets, outputs, prompts, home):
        folder.mkdir(parents=True, exist_ok=True)
    packet_path = packets / f"{setup_id}.json"
    prompt_path = prompts / f"{setup_id}.txt"
    output_path = outputs / f"{setup_id}.json"
    packet_path.write_text(json.dumps(packet, indent=2, sort_keys=True, default=str), encoding="utf-8")
    prompt_path.write_text(research_prompt(packet), encoding="utf-8")
    return prompt_path, output_path


def _mark_job(
    connection: sqlite3.Connection,
    setup_id: str,
    status: str,
    *,
    packet_path: Path | None = None,
    output_path: Path | None = None,
    error: str = "",
    completed: bool = False,
) -> None:
    now = _utc_iso()
    connection.execute(
        """
        UPDATE vibe_research_jobs_v02
        SET status=?,updated_at=?,completed_at=?,packet_path=COALESCE(NULLIF(?,''),packet_path),
            output_path=COALESCE(NULLIF(?,''),output_path),last_error=?
        WHERE setup_id=?
        """,
        (
            status,
            now,
            now if completed else None,
            str(packet_path or ""),
            str(output_path or ""),
            str(error or "")[:2000],
            setup_id,
        ),
    )
    connection.commit()


def _parse_vibe_output(raw: str) -> dict[str, Any]:
    text = str(raw or "").strip()
    if not text:
        return {"status": "EMPTY_OUTPUT"}
    try:
        loaded = json.loads(text)
        return loaded if isinstance(loaded, dict) else {"result": loaded}
    except json.JSONDecodeError:
        # Some CLI versions may wrap the agent answer in logging text. Preserve
        # raw output instead of inventing a research conclusion.
        return {"status": "UNPARSED_OUTPUT", "text": text[-12000:]}


def _run_vibe(config: VibeResearchConfig, prompt_path: Path) -> subprocess.CompletedProcess[str]:
    command = [
        str(config.executable),
        "run",
        "-f",
        str(prompt_path),
        "--json",
        "--no-rich",
        "--max-iter",
        str(config.max_iter),
    ]
    return subprocess.run(
        command,
        cwd=str(config.workdir),
        env=safe_vibe_environment(config),
        capture_output=True,
        text=True,
        timeout=config.timeout_seconds,
        check=False,
    )


def process_one_vibe_job(
    connection: sqlite3.Connection,
    *,
    config: VibeResearchConfig | None = None,
    runner=None,
) -> dict[str, Any] | None:
    """Capture and, when configured, analyze one closed Gold trade with Vibe."""
    config = config or load_vibe_research_config()
    setup_id = _claim_next(connection, config)
    if setup_id is None:
        return None

    try:
        packet = build_vibe_packet(connection, setup_id)
        prompt_path, output_path = _write_packet(config, setup_id, packet)
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        _mark_job(connection, setup_id, "ERROR", error=message, completed=True)
        return {"setup_id": setup_id, "status": "ERROR", "error": message}

    packet_path = config.workdir / "packets" / f"{setup_id}.json"
    if not config.package_installed:
        _mark_job(
            connection,
            setup_id,
            "PACKAGE_UNAVAILABLE",
            packet_path=packet_path,
            error=f"Expected isolated Vibe-Trading {VIBE_VERSION} executable at {config.executable}",
        )
        return {"setup_id": setup_id, "status": "PACKAGE_UNAVAILABLE"}

    if not config.provider_ready:
        _mark_job(
            connection,
            setup_id,
            "WAITING_PROVIDER",
            packet_path=packet_path,
            error=f"Vibe packet captured; provider status is {config.provider_status}.",
        )
        return {"setup_id": setup_id, "status": "WAITING_PROVIDER"}

    _mark_job(connection, setup_id, "RUNNING", packet_path=packet_path)
    execute = runner or _run_vibe
    try:
        result = execute(config, prompt_path)
    except subprocess.TimeoutExpired as exc:
        message = f"Vibe research timed out after {config.timeout_seconds}s: {exc}"
        _mark_job(connection, setup_id, "RETRY", packet_path=packet_path, error=message)
        return {"setup_id": setup_id, "status": "RETRY", "error": message}
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        _mark_job(connection, setup_id, "RETRY", packet_path=packet_path, error=message)
        return {"setup_id": setup_id, "status": "RETRY", "error": message}

    stdout = str(getattr(result, "stdout", "") or "")
    stderr = str(getattr(result, "stderr", "") or "")
    returncode = int(getattr(result, "returncode", 1))
    if returncode != 0:
        message = (stderr or stdout or f"Vibe exited with code {returncode}")[-2000:]
        _mark_job(connection, setup_id, "RETRY", packet_path=packet_path, error=message)
        return {"setup_id": setup_id, "status": "RETRY", "error": message}

    parsed = _parse_vibe_output(stdout)
    output_path.write_text(json.dumps(parsed, indent=2, sort_keys=True, default=str), encoding="utf-8")
    connection.execute(
        """
        INSERT INTO vibe_research_findings_v02(
            setup_id,captured_at,provider,model,package_version,result_json,raw_output,authority
        ) VALUES (?,?,?,?,?,?,?,'NON_AUTHORITATIVE_RESEARCH_ONLY')
        ON CONFLICT(setup_id) DO UPDATE SET
            captured_at=excluded.captured_at,provider=excluded.provider,model=excluded.model,
            package_version=excluded.package_version,result_json=excluded.result_json,
            raw_output=excluded.raw_output,authority=excluded.authority
        """,
        (
            setup_id,
            _utc_iso(),
            config.provider,
            config.model,
            VIBE_VERSION,
            json.dumps(parsed, sort_keys=True, default=str),
            stdout[-20000:],
        ),
    )
    connection.commit()
    _mark_job(
        connection,
        setup_id,
        "COMPLETE",
        packet_path=packet_path,
        output_path=output_path,
        completed=True,
    )
    return {"setup_id": setup_id, "status": "COMPLETE", "result": parsed}


def _worker_loop() -> None:
    config = load_vibe_research_config()
    print(
        "Vibe research sidecar started: "
        f"v{VIBE_VERSION}, poll={config.poll_seconds:.2f}s, provider={config.provider_status}, "
        "fail-open and non-authoritative.",
        flush=True,
    )
    while not _worker_stop.is_set():
        connection = None
        result = None
        try:
            config = load_vibe_research_config()
            connection = get_connection()
            result = process_one_vibe_job(connection, config=config)
        except Exception as exc:
            print(f"Vibe research worker warning: {type(exc).__name__}: {exc}", flush=True)
        finally:
            if connection is not None:
                connection.close()

        if result:
            status = result.get("status")
            setup_id = result.get("setup_id")
            if status == "COMPLETE":
                print(f"Vibe research stored finding for {setup_id}.", flush=True)
            elif status in {"ERROR", "PACKAGE_UNAVAILABLE", "RETRY"}:
                message = str(result.get("error", "") or "").replace("\n", " ")[-800:]
                print(f"Vibe research {status.lower()} for {setup_id}: {message}", flush=True)
            # WAITING_PROVIDER is a normal capture-only state. Do not hammer the
            # same job repeatedly while credentials are intentionally absent.
            if status == "WAITING_PROVIDER":
                _worker_stop.wait(max(config.poll_seconds, 10.0))
            continue
        _worker_stop.wait(config.poll_seconds)


def start_vibe_research_worker() -> bool:
    """Start one dashboard-side Vibe worker. Safe to call repeatedly."""
    config = load_vibe_research_config()
    if not config.enabled:
        return False

    global _worker_thread
    with _worker_lock:
        if _worker_thread is not None and _worker_thread.is_alive():
            return True
        connection = None
        try:
            connection = get_connection()
            recovered = recover_interrupted_vibe_jobs(connection)
            if recovered:
                print(f"Vibe research requeued {recovered} interrupted job(s).", flush=True)
        except Exception as exc:
            print(f"Vibe research recovery warning: {type(exc).__name__}: {exc}", flush=True)
        finally:
            if connection is not None:
                connection.close()
        _worker_stop.clear()
        _worker_thread = threading.Thread(target=_worker_loop, name=_WORKER_NAME, daemon=True)
        _worker_thread.start()
        return True


def stop_vibe_research_worker_for_tests(timeout: float = 2.0) -> None:
    global _worker_thread
    _worker_stop.set()
    thread = _worker_thread
    if thread is not None and thread.is_alive():
        thread.join(timeout=max(0.0, float(timeout)))
    _worker_thread = None


def vibe_research_snapshot(connection: sqlite3.Connection, *, recent_limit: int = 12) -> dict[str, Any]:
    config = load_vibe_research_config()
    ensure_vibe_research_schema(connection)
    counts = {
        str(status): int(count)
        for status, count in connection.execute(
            "SELECT status,COUNT(*) FROM vibe_research_jobs_v02 GROUP BY status"
        ).fetchall()
    }
    jobs = connection.execute(
        """
        SELECT setup_id,status,attempts,queued_at,updated_at,completed_at,provider,model,last_error
        FROM vibe_research_jobs_v02
        ORDER BY updated_at DESC LIMIT ?
        """,
        (max(1, min(int(recent_limit), 50)),),
    ).fetchall()
    findings = connection.execute(
        """
        SELECT setup_id,captured_at,provider,model,result_json
        FROM vibe_research_findings_v02
        ORDER BY captured_at DESC LIMIT ?
        """,
        (max(1, min(int(recent_limit), 50)),),
    ).fetchall()
    thread = _worker_thread
    return {
        "authoritative": False,
        "strategy_mutation_allowed": False,
        "risk_mutation_allowed": False,
        "broker_access_allowed": False,
        "enabled": config.enabled,
        "worker_running": bool(thread is not None and thread.is_alive()),
        "package_version": VIBE_VERSION,
        "package_installed": config.package_installed,
        "provider": config.provider or None,
        "model": config.model or None,
        "provider_status": config.provider_status,
        "poll_seconds": config.poll_seconds,
        "counts": counts,
        "recent_jobs": [
            {
                "setup_id": row[0],
                "status": row[1],
                "attempts": int(row[2] or 0),
                "queued_at": row[3],
                "updated_at": row[4],
                "completed_at": row[5],
                "provider": row[6] or None,
                "model": row[7] or None,
                "last_error": row[8] or "",
            }
            for row in jobs
        ],
        "recent_findings": [
            {
                "setup_id": row[0],
                "captured_at": row[1],
                "provider": row[2],
                "model": row[3],
                "result": json.loads(row[4]) if row[4] else {},
            }
            for row in findings
        ],
    }
