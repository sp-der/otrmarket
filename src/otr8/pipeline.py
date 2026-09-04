from __future__ import annotations

import json
import os
from datetime import timezone

from .arbiter import SetupArbiter80
from .candidates import CandidateCollector80
from .models import DecisionTrace80, TradePlan80
from .regime import GoldRegimeEngine80


class OTRPipeline80:
    """One explicit decision path from candidate collection to execution handoff.

    Operation 8.0 does not weaken the inherited trading contract. Session,
    quality, cooldown, Gold 1m firewall, evaluation, no-chase, geometry and the
    executor's one-symbol invariant remain authoritative. This class makes the
    order of those decisions explicit and records a complete trace.
    """

    def __init__(
        self,
        *,
        runtime,
        session_gate,
        quality_gate,
        setup_risk,
        continuation=None,
        shadow_register=None,
        counterfactual_module=None,
        observer=None,
        mode_provider=None,
        arbiter=None,
        regime_engine=None,
    ) -> None:
        self.runtime = runtime
        self.session_gate = session_gate
        self.quality_gate = quality_gate
        self.setup_risk = setup_risk
        self.shadow_register = shadow_register
        self.counterfactual_module = counterfactual_module
        self.observer = observer
        self.mode_provider = mode_provider or (lambda: os.getenv("OTR_TRADING_MODE", ""))
        self.collector = CandidateCollector80(runtime.strategy, continuation=continuation)
        self.arbiter = arbiter or SetupArbiter80()
        self.regime_engine = regime_engine or GoldRegimeEngine80()

    @staticmethod
    def _ensure_trace_schema(connection) -> None:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS decision_traces_80 (
                setup_id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                strategy TEXT NOT NULL,
                direction TEXT NOT NULL,
                source TEXT NOT NULL,
                final_status TEXT NOT NULL,
                trace_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_decision_traces_80_symbol_time
            ON decision_traces_80(symbol, timeframe, created_at);
            """
        )
        connection.commit()

    def _persist_trace(self, connection, trace: DecisionTrace80) -> None:
        self._ensure_trace_schema(connection)
        payload = trace.to_dict()
        timestamp = trace.created_at
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        stamp = timestamp.astimezone(timezone.utc).isoformat()
        connection.execute(
            """
            INSERT INTO decision_traces_80(
                setup_id,symbol,timeframe,strategy,direction,source,final_status,
                trace_json,created_at,updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(setup_id) DO UPDATE SET
                final_status=excluded.final_status,
                trace_json=excluded.trace_json,
                updated_at=excluded.updated_at
            """,
            (
                trace.setup_id,
                trace.symbol,
                trace.timeframe,
                trace.strategy,
                trace.direction,
                trace.source,
                trace.final_status,
                json.dumps(payload, sort_keys=True, default=str),
                stamp,
                stamp,
            ),
        )
        connection.commit()

    def _trace_for(self, setup, source: str) -> DecisionTrace80:
        return DecisionTrace80(
            setup_id=str(setup.setup_id),
            symbol=str(setup.symbol),
            timeframe=str(setup.timeframe),
            strategy=str(setup.metadata.get("strategy", "ICT_CONFLUENCE")),
            direction=str(setup.direction),
            created_at=setup.created_at,
            source=source,
        )

    def _track_blocked(self, connection, setup) -> None:
        module = self.counterfactual_module
        if module is None:
            return
        try:
            module._track_blocked(connection, setup)
            module._remember_failed_thesis(setup)
        except Exception as exc:
            self.runtime.console.log(f"OTR 8.0 counterfactual tracking warning: {exc}")

    @staticmethod
    def _generic_counterfactual(connection, setup, reason: str) -> None:
        try:
            connection.execute(
                """
                INSERT OR IGNORE INTO counterfactual_setups(
                    setup_id,symbol,timeframe,direction,entry_price,stop_price,
                    target_price,created_at,blocked_status,blocked_reason
                ) VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    setup.setup_id,
                    setup.symbol,
                    setup.timeframe,
                    setup.direction,
                    float(setup.entry_price),
                    float(setup.stop_price),
                    float(setup.target_price),
                    setup.created_at.isoformat(),
                    str(setup.status),
                    str(reason),
                ),
            )
            connection.commit()
        except Exception:
            pass

    def _save_block(self, connection, setup, trace, status: str, stage: str, reason: str, details=None):
        setup.status = status
        setup.metadata.setdefault("execution_quality_gate", {}).update(
            {"allowed": False, "reason": reason, "profile": "OTR_PIPELINE_8_0"}
        )
        self.runtime.save_setup(connection, setup)
        trace.add(stage, "BLOCKED", reason, details)
        trace.finish(status)
        self._persist_trace(connection, trace)
        self._track_blocked(connection, setup)
        return setup

    def _regime(self, setup, histories):
        return self.regime_engine.classify(
            histories,
            setup.symbol,
            setup.timeframe,
            setup.created_at,
        )

    def _prepare_eligible(self, connection, candidates, histories, source: str):
        eligible = []
        traces = {}
        regimes = {}
        handled = []

        for setup in candidates:
            trace = self._trace_for(setup, source)
            traces[str(setup.setup_id)] = trace
            regime = self._regime(setup, histories)
            regimes[str(setup.setup_id)] = regime
            setup.metadata["gold_regime_80"] = regime.to_dict()
            trace.add("REGIME", "OBSERVED", f"{regime.regime} / {regime.direction}", regime.to_dict())

            if source == "CANDLE_CLOSE" and self.shadow_register is not None:
                if str(setup.metadata.get("strategy", "")) != "MSS_REVERSAL":
                    try:
                        self.shadow_register(connection, setup)
                    except Exception as exc:
                        trace.add("SHADOW", "WARNING", str(exc))

            session = self.session_gate(connection, setup)
            setup.metadata["session_consistency"] = session.details
            if not session.allowed:
                handled.append(
                    self._save_block(
                        connection,
                        setup,
                        trace,
                        "SESSION_BLOCKED",
                        "SESSION",
                        session.reason,
                        session.details,
                    )
                )
                continue
            trace.add("SESSION", "PASSED", session.reason, session.details)

            allowed, reason = self.quality_gate(connection, setup, histories)
            setup.metadata["execution_quality_gate"] = {
                "allowed": bool(allowed),
                "reason": reason,
                "profile": "OTR_PIPELINE_8_0",
            }
            if not allowed:
                handled.append(
                    self._save_block(
                        connection,
                        setup,
                        trace,
                        "QUALITY_BLOCKED",
                        "QUALITY",
                        reason,
                        setup.metadata.get("a_plus_context", {}),
                    )
                )
                continue
            trace.add("QUALITY", "PASSED", reason, setup.metadata.get("a_plus_context", {}))
            eligible.append(setup)

        return eligible, traces, regimes, handled

    def process_candidates(self, connection, candidates, histories, *, source: str = "CANDLE_CLOSE"):
        if not candidates:
            return []

        eligible, traces, regimes, handled = self._prepare_eligible(
            connection, candidates, histories, source
        )
        if not eligible:
            return handled

        chosen, assessments = self.arbiter.choose(eligible, histories, regimes)
        assessment_by_id = {item.setup_id: item for item in assessments}
        chosen_id = str(chosen.setup_id) if chosen is not None else ""

        for setup in eligible:
            setup_id = str(setup.setup_id)
            trace = traces[setup_id]
            assessment = assessment_by_id[setup_id]
            trace.add("ARBITER", "SELECTED" if setup_id == chosen_id else "BLOCKED", (
                f"Candidate score {assessment.score:.2f}/100"
                if setup_id == chosen_id
                else setup.metadata["setup_arbiter_80"]["reason"]
            ), assessment.to_dict())
            if setup_id == chosen_id:
                continue
            setup.status = "ARBITER_BLOCKED"
            reason = setup.metadata["setup_arbiter_80"]["reason"]
            setup.metadata["execution_quality_gate"] = {
                "allowed": False,
                "reason": reason,
                "profile": "SETUP_ARBITER_8_0",
            }
            self.runtime.save_setup(connection, setup)
            self._generic_counterfactual(connection, setup, reason)
            trace.finish("ARBITER_BLOCKED")
            self._persist_trace(connection, trace)
            handled.append(setup)

        if chosen is None:
            return handled

        trace = traces[chosen_id]
        decision = self.runtime.evaluation_guard.decide(connection, chosen.created_at)
        applied_risk, risk_multiplier = self.setup_risk(decision, chosen)
        chosen.metadata["evaluation_guard"] = {
            "status": decision.status,
            "allowed": decision.allowed,
            "risk_cap_dollars": decision.risk_dollars,
            "risk_multiplier": risk_multiplier,
            "risk_dollars": applied_risk if decision.allowed else 0.0,
            "reason": decision.reason,
            "profile": decision.snapshot.get("profile"),
            "phase": decision.snapshot.get("phase"),
        }
        if not decision.allowed:
            handled.append(
                self._save_block(
                    connection,
                    chosen,
                    trace,
                    "GUARD_BLOCKED",
                    "ACCOUNT_GUARD",
                    decision.reason,
                    decision.snapshot,
                )
            )
            return handled
        trace.add("ACCOUNT_GUARD", "PASSED", decision.reason, decision.snapshot)

        assessment = assessment_by_id[chosen_id]
        regime = regimes[chosen_id]
        grade = str(
            chosen.metadata.get("a_plus_context", {}).get("quality_grade")
            or assessment.details.get("quality_grade")
            or "A"
        )
        plan = TradePlan80(
            setup_id=chosen_id,
            symbol=str(chosen.symbol),
            timeframe=str(chosen.timeframe),
            strategy=str(chosen.metadata.get("strategy", "ICT_CONFLUENCE")),
            direction=str(chosen.direction),
            entry_price=float(chosen.entry_price),
            stop_price=float(chosen.stop_price),
            target_price=float(chosen.target_price),
            risk_reward=float(chosen.risk_reward),
            risk_dollars=float(applied_risk),
            quality_grade=grade,
            arbiter_score=float(assessment.score),
            regime=regime.regime,
            created_at=chosen.created_at,
            source=source,
            metadata={
                "risk_multiplier": risk_multiplier,
                "session_tier": chosen.metadata.get("session_tier"),
                "entry_type": chosen.metadata.get("entry_type"),
            },
        )
        chosen.metadata["trade_plan_80"] = plan.to_dict()
        trace.add("TRADE_PLAN", "CREATED", "Canonical strategy-side trade plan created.", plan.to_dict())

        self.runtime.save_setup(connection, chosen)
        try:
            position = self.runtime.paper.register_setup(
                chosen,
                risk_dollars=applied_risk,
                guard_reason=(
                    f"{decision.reason} OTR 8.0 pipeline selected candidate at "
                    f"{assessment.score:.2f}/100; risk tier {risk_multiplier:.0%}."
                ),
            )
        except ValueError as exc:
            chosen.status = "RISK_REJECTED"
            chosen.metadata["geometry_rejection"] = str(exc)
            self.runtime.save_setup(connection, chosen)
            trace.add("EXECUTOR_PREFLIGHT", "BLOCKED", str(exc))
            trace.finish("RISK_REJECTED")
            self._persist_trace(connection, trace)
            handled.append(chosen)
            return handled

        self.runtime.upsert_paper_trade(connection, position, chosen.created_at.isoformat())
        final_status = str(position.result or position.status or "PENDING")
        trace.add(
            "EXECUTION_HANDOFF",
            "ACCEPTED" if str(position.status).upper() in {"PENDING", "OPEN"} else "SUPPRESSED",
            f"Paper/execution kernel returned {final_status}.",
            {"position_status": position.status, "result": position.result, "risk_dollars": applied_risk},
        )
        trace.finish(final_status)
        self._persist_trace(connection, trace)
        handled.append(chosen)
        self.runtime.console.log(
            f"OTR 8.0 SELECTED {chosen.symbol} {chosen.timeframe} "
            f"[{chosen.metadata.get('strategy', 'UNKNOWN')}] {chosen.direction.upper()} "
            f"score={assessment.score:.2f}/100 rr={chosen.risk_reward:.2f}R "
            f"regime={regime.regime} risk=${applied_risk:.2f} result={final_status}"
        )
        return handled

    def evaluate(self, connection, symbol: str, timeframe: str):
        if str(timeframe).lower() == "4h":
            return None

        histories = self.runtime.histories_snapshot()
        if self.counterfactual_module is not None:
            try:
                self.counterfactual_module._ensure_counterfactual_table(connection)
                self.counterfactual_module._update_counterfactuals(
                    connection, symbol, timeframe, histories
                )
            except Exception as exc:
                self.runtime.console.log(f"OTR 8.0 counterfactual update warning: {exc}")

        if not self.runtime.session.strategy_enabled(symbol):
            if self.observer is not None:
                self.observer(connection, symbol, timeframe, histories)
            return None

        candidates = self.collector.collect(
            symbol,
            timeframe,
            histories,
            self.mode_provider(),
        )
        self.runtime.save_diagnostic(
            connection,
            self.runtime.strategy.diagnostic(symbol, timeframe),
        )
        handled = self.process_candidates(
            connection,
            candidates,
            histories,
            source="CANDLE_CLOSE",
        )
        if self.observer is not None:
            self.observer(connection, symbol, timeframe, histories)
        return handled[-1] if handled else None
