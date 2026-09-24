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
        # Opt-in hooks used by Operation 8.1 only. Defaults preserve Operation
        # 8.0 behavior and its existing regression contract.
        self.promote_runner_up = False
        self.evaluation_recorder = None

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

            if self.promote_runner_up and setup.metadata.get("preview_only_80"):
                setup.status = "PRE_ARMED"
                self.runtime.save_setup(connection, setup)
                trace.add("DEVELOPING", "PRE_ARMED", "Waiting for qualifying pullback / fresh entry geometry; observation only.")
                trace.finish("PRE_ARMED")
                self._persist_trace(connection, trace)
                handled.append(setup)
                continue

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

    def _process_candidates_with_promotion(self, connection, candidates, histories, *, source: str):
        """Operation 8.1 ranked fallback.

        Every candidate still clears session + quality before ranking. A lower
        ranked candidate is considered only when a higher-ranked candidate
        fails executor preflight/geometry. Account-wide guard failures and
        active-symbol conflicts never fall through, so promotion cannot bypass
        risk controls or create a second Gold position.
        """
        if not candidates:
            return []

        eligible, traces, regimes, handled = self._prepare_eligible(
            connection, candidates, histories, source
        )
        if not eligible:
            return handled

        _chosen, assessments = self.arbiter.choose(eligible, histories, regimes)
        setup_by_id = {str(setup.setup_id): setup for setup in eligible}
        ranked = sorted(
            assessments,
            key=lambda item: (item.score, item.risk_reward, item.setup_id),
            reverse=True,
        )
        assessment_payload = [item.to_dict() for item in ranked]
        attempted: set[str] = set()
        winner = None
        winner_assessment = None
        terminal_reason = ""

        for rank, assessment in enumerate(ranked, start=1):
            setup_id = str(assessment.setup_id)
            setup = setup_by_id[setup_id]
            trace = traces[setup_id]
            attempted.add(setup_id)
            decision_time = setup.created_at
            if rank > 1:
                from .runner_up81 import check_runner_up81, session_probe
                status, reason, decision_time, causal = check_runner_up81(setup, histories, self.runtime)
                if status is None:
                    session = self.session_gate(connection, session_probe(setup, decision_time))
                    if not session.allowed:
                        status, reason = "RUNNER_UP_SESSION_BLOCKED", session.reason
                if status is None:
                    allowed, reason = self.quality_gate(connection, setup, causal)
                    if not allowed:
                        status = "RUNNER_UP_QUALITY_NO_LONGER_VALID"
                if status is None:
                    # Quality can adjust entry geometry; check the final executable plan again.
                    status, reason, decision_time, causal = check_runner_up81(setup, causal, self.runtime)
                if status:
                    handled.append(self._save_block(connection, setup, trace, status,
                                                    "RUNNER_UP_VALIDATION", reason))
                    continue
                trace.add("RUNNER_UP_VALIDATION", "PASSED", reason,
                          {"event_time": decision_time.isoformat()})
            setup.metadata["setup_arbiter_80"] = {
                "selected": True,
                "score": assessment.score,
                "promoted_rank": rank,
                "assessments": assessment_payload,
                "reason": (
                    "Top-ranked qualified candidate."
                    if rank == 1
                    else f"Promoted after {rank - 1} higher-ranked candidate(s) failed executor preflight."
                ),
            }
            trace.add(
                "ARBITER",
                "SELECTED" if rank == 1 else "ARBITER_PROMOTED_RUNNER_UP",
                (
                    f"Candidate score {assessment.score:.2f}/100"
                    if rank == 1
                    else f"Runner-up promoted at rank {rank}; score {assessment.score:.2f}/100."
                ),
                assessment.to_dict(),
            )

            decision = self.runtime.evaluation_guard.decide(connection, decision_time)
            applied_risk, risk_multiplier = self.setup_risk(decision, setup)
            setup.metadata["evaluation_guard"] = {
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
                terminal_reason = decision.reason
                handled.append(
                    self._save_block(
                        connection,
                        setup,
                        trace,
                        "GUARD_BLOCKED",
                        "ACCOUNT_GUARD",
                        decision.reason,
                        decision.snapshot,
                    )
                )
                break
            trace.add("ACCOUNT_GUARD", "PASSED", decision.reason, decision.snapshot)

            regime = regimes[setup_id]
            grade = str(
                setup.metadata.get("a_plus_context", {}).get("quality_grade")
                or assessment.details.get("quality_grade")
                or "A"
            )
            plan = TradePlan80(
                setup_id=setup_id,
                symbol=str(setup.symbol),
                timeframe=str(setup.timeframe),
                strategy=str(setup.metadata.get("strategy", "ICT_CONFLUENCE")),
                direction=str(setup.direction),
                entry_price=float(setup.entry_price),
                stop_price=float(setup.stop_price),
                target_price=float(setup.target_price),
                risk_reward=float(setup.risk_reward),
                risk_dollars=float(applied_risk),
                quality_grade=grade,
                arbiter_score=float(assessment.score),
                regime=regime.regime,
                created_at=setup.created_at,
                source=source,
                metadata={
                    "risk_multiplier": risk_multiplier,
                    "session_tier": setup.metadata.get("session_tier"),
                    "entry_type": setup.metadata.get("entry_type"),
                    "promoted_rank": rank,
                },
            )
            setup.metadata["trade_plan_80"] = plan.to_dict()
            trace.add("TRADE_PLAN", "CREATED", "Canonical strategy-side trade plan created.", plan.to_dict())

            self.runtime.save_setup(connection, setup)
            try:
                position = self.runtime.paper.register_setup(
                    setup,
                    risk_dollars=applied_risk,
                    guard_reason=(
                        f"{decision.reason} OTR 8.1 ranked candidate {rank} at "
                        f"{assessment.score:.2f}/100; risk tier {risk_multiplier:.0%}."
                    ),
                )
            except ValueError as exc:
                message = str(exc)
                terminal_reason = message
                setup.status = "RISK_REJECTED"
                setup.metadata["geometry_rejection"] = message
                setup.metadata.setdefault("setup_arbiter_80", {})["preflight_failed"] = True
                self.runtime.save_setup(connection, setup)
                trace.add("EXECUTOR_PREFLIGHT", "BLOCKED", message)
                trace.finish("RISK_REJECTED")
                self._persist_trace(connection, trace)
                self._generic_counterfactual(connection, setup, message)
                handled.append(setup)
                # Existing exposure is account state, not candidate geometry.
                # Never use runner-up promotion to route around that invariant.
                recoverable = any(token in message.lower() for token in (
                    "geometry", "tick size", "risk distance", "reward distance", "cannot_size_mgc"
                ))
                if "ACTIVE_SYMBOL_CONFLICT" in message.upper() or not recoverable:
                    break
                continue

            self.runtime.upsert_paper_trade(connection, position, setup.created_at.isoformat())
            final_status = str(position.result or position.status or "PENDING")
            if (
                str(getattr(position, "status", "") or "").upper() == "INVALIDATED"
                and str(getattr(position, "result", "") or "").upper() == "CANNOT_SIZE_MGC"
            ):
                terminal_reason = final_status
                setup.status = "RISK_REJECTED"
                setup.metadata["geometry_rejection"] = final_status
                setup.metadata.setdefault("setup_arbiter_80", {})["preflight_failed"] = True
                self.runtime.save_setup(connection, setup)
                trace.add(
                    "EXECUTOR_PREFLIGHT",
                    "BLOCKED",
                    "Selected geometry could not fund one whole MGC contract inside the risk cap.",
                    {"position_status": position.status, "result": position.result},
                )
                trace.finish("RISK_REJECTED")
                self._persist_trace(connection, trace)
                self._generic_counterfactual(connection, setup, final_status)
                handled.append(setup)
                continue
            trace.add(
                "EXECUTION_HANDOFF",
                "ACCEPTED" if str(position.status).upper() in {"PENDING", "OPEN"} else "SUPPRESSED",
                f"Paper/execution kernel returned {final_status}.",
                {"position_status": position.status, "result": position.result, "risk_dollars": applied_risk},
            )
            trace.finish(final_status)
            self._persist_trace(connection, trace)
            handled.append(setup)
            self.runtime.console.log(
                f"OTR 8.1 SELECTED {setup.symbol} {setup.timeframe} "
                f"[{setup.metadata.get('strategy', 'UNKNOWN')}] {setup.direction.upper()} "
                f"rank={rank} score={assessment.score:.2f}/100 rr={setup.risk_reward:.2f}R "
                f"regime={regime.regime} risk=${applied_risk:.2f} result={final_status}"
            )
            winner = setup
            winner_assessment = assessment
            break

        for assessment in ranked:
            setup_id = str(assessment.setup_id)
            if setup_id in attempted or (winner is not None and setup_id == str(winner.setup_id)):
                continue
            setup = setup_by_id[setup_id]
            trace = traces[setup_id]
            if winner is not None and winner_assessment is not None:
                reason = (
                    f"Another executable GC candidate ranked/converted first: "
                    f"{winner.metadata.get('strategy', 'UNKNOWN')} "
                    f"{winner_assessment.score:.2f} > {assessment.score:.2f}."
                )
            else:
                reason = (
                    "No lower-ranked promotion attempted because the selected candidate "
                    f"hit an account-wide terminal condition: {terminal_reason or 'guard/preflight stop'}."
                )
            setup.status = "ARBITER_BLOCKED"
            setup.metadata["setup_arbiter_80"] = {
                "selected": False,
                "score": assessment.score,
                "winner_setup_id": str(getattr(winner, "setup_id", "") or ""),
                "winner_score": getattr(winner_assessment, "score", None),
                "reason": reason,
                "promotion_considered": True,
            }
            setup.metadata["execution_quality_gate"] = {
                "allowed": False,
                "reason": reason,
                "profile": "SETUP_ARBITER_8_1",
            }
            self.runtime.save_setup(connection, setup)
            self._generic_counterfactual(connection, setup, reason)
            trace.add("ARBITER", "BLOCKED", reason, assessment.to_dict())
            trace.finish("ARBITER_BLOCKED")
            self._persist_trace(connection, trace)
            handled.append(setup)

        return handled

    def process_candidates(self, connection, candidates, histories, *, source: str = "CANDLE_CLOSE"):
        if self.promote_runner_up:
            return self._process_candidates_with_promotion(
                connection, candidates, histories, source=source
            )
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
        if self.promote_runner_up:
            from .runner_up81 import utc
            try:
                cutoff = self.runtime.clock.event_time(symbol)
            except AttributeError:
                cutoff = None
            bars = histories.get((symbol, timeframe), [])
            if cutoff is None and bars:
                cutoff = bars[-1].close_time
            if cutoff is not None:
                histories = {key: [bar for bar in values if utc(bar.close_time) <= utc(cutoff)]
                             for key, values in histories.items()}
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
        if self.evaluation_recorder is not None:
            try:
                self.evaluation_recorder(
                    connection,
                    symbol,
                    timeframe,
                    histories,
                    candidates,
                    handled,
                    source="CANDLE_CLOSE",
                )
            except Exception as exc:
                self.runtime.console.log(f"OTR 8.1 decision-recorder warning: {exc}")
        if self.observer is not None:
            self.observer(connection, symbol, timeframe, histories)
        return handled[-1] if handled else None
