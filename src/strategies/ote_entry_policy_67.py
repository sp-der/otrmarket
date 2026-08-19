from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

from src.strategies.flexible_confluence import FlexibleConfluenceEngine


NY = ZoneInfo("America/New_York")


class OTEEntryPolicy67(FlexibleConfluenceEngine):
    """Operation 6.7 entry-quality policy using the user's Fib template.

    Fib template: 0 / .50 / .618 / .705 / .79 / .88 / 1.00.
    The standard OTE execution zone is .705-.79, with .79 preferred when valid.
    Entries shallower than .705 are aggressive fallbacks only and must clear the
    full confirmation checklist at reduced risk. An instant-stop memory can
    disable shallow follow-up entries for the same symbol/timeframe/direction
    for the rest of that New York trading date.
    """

    FIB_LEVELS = (0.0, 0.50, 0.618, 0.705, 0.79, 0.88, 1.0)
    OTE_MIN = 0.705
    OTE_MAX = 0.79
    SHALLOW_RISK_CAP = 0.60
    VERY_SHALLOW_RISK_CAP = 0.40
    STRONG_BODY_RATIO = 1.90
    STRONG_RANGE_RATIO = 1.50

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._instant_stop_penalty_dates: dict[tuple[str, str, str], set[str]] = {}

    @staticmethod
    def _trading_date(value: datetime) -> str:
        if value.tzinfo is None:
            value = value.replace(tzinfo=NY)
        return value.astimezone(NY).date().isoformat()

    @staticmethod
    def _retracement_fraction(context, price: float) -> float | None:
        displacement = context.displacement
        if displacement is None:
            return None
        move_range = float(displacement.high) - float(displacement.low)
        if move_range <= 0:
            return None
        if context.direction == "bullish":
            return (float(displacement.high) - float(price)) / move_range
        return (float(price) - float(displacement.low)) / move_range

    @classmethod
    def _entry_zone(cls, retracement: float | None) -> str:
        if retracement is None:
            return "UNKNOWN"
        if cls.OTE_MIN <= retracement <= cls.OTE_MAX + 1e-9:
            return "OTE_705_79"
        if 0.50 <= retracement < cls.OTE_MIN:
            return "SHALLOW_AGGRESSIVE"
        if cls.OTE_MAX < retracement <= 0.88:
            return "DEEP_79_88"
        return "OUTSIDE_TEMPLATE"

    @classmethod
    def _fib_profile(cls) -> dict:
        return {
            "levels": list(cls.FIB_LEVELS),
            "standard_ote_zone": [cls.OTE_MIN, cls.OTE_MAX],
            "preferred_deep_ote": cls.OTE_MAX,
            "shallow_boundary": cls.OTE_MIN,
        }

    @staticmethod
    def _aggressive_checklist(candles, context, entry_fvg) -> dict:
        displacement = context.displacement
        body_ratio = float(getattr(displacement, "body_ratio", 0.0) or 0.0)
        range_ratio = float(getattr(displacement, "range_ratio", 0.0) or 0.0)
        strong_displacement = body_ratio >= 1.90 and range_ratio >= 1.50

        sweep_complete = (
            str(context.trigger_type or "").lower() == "liquidity_sweep"
            and context.swept_level is not None
        )

        latest = candles[-1] if candles else None
        prior = candles[-4:-1] if len(candles) >= 4 else []
        if latest is None or not prior:
            micro_structure_shift = False
        elif context.direction == "bullish":
            micro_structure_shift = float(latest.close) > max(float(c.high) for c in prior)
        else:
            micro_structure_shift = float(latest.close) < min(float(c.low) for c in prior)

        if latest is None or entry_fvg is None:
            fvg_retest_holds = False
        elif context.direction == "bullish":
            fvg_retest_holds = (
                float(latest.low) > float(entry_fvg.lower)
                and float(latest.close) >= float(entry_fvg.midpoint)
            )
        else:
            fvg_retest_holds = (
                float(latest.high) < float(entry_fvg.upper)
                and float(latest.close) <= float(entry_fvg.midpoint)
            )

        no_opposing_pressure = bool(
            latest
            and (
                (context.direction == "bullish" and float(latest.close) >= float(latest.open))
                or (context.direction == "bearish" and float(latest.close) <= float(latest.open))
            )
        )

        checks = {
            "strong_displacement": strong_displacement,
            "liquidity_sweep_complete": sweep_complete,
            "micro_structure_shift": micro_structure_shift,
            "fvg_retest_holds": fvg_retest_holds,
            "no_immediate_opposing_pressure": no_opposing_pressure,
            "body_ratio": round(body_ratio, 4),
            "range_ratio": round(range_ratio, 4),
        }
        checks["all_confirmed"] = all(
            checks[name]
            for name in (
                "strong_displacement",
                "liquidity_sweep_complete",
                "micro_structure_shift",
                "fvg_retest_holds",
                "no_immediate_opposing_pressure",
            )
        )
        return checks

    def _penalty_active(self, context, market_time: datetime) -> bool:
        key = (context.symbol, context.timeframe, context.direction)
        return self._trading_date(market_time) in self._instant_stop_penalty_dates.get(key, set())

    def record_instant_stop(self, position) -> None:
        if str(getattr(position, "result", "") or "").upper() != "LOSS":
            return
        opened_at = getattr(position, "opened_at", None)
        closed_at = getattr(position, "closed_at", None)
        if opened_at is None or closed_at is None:
            return
        try:
            seconds = max(0.0, (closed_at - opened_at).total_seconds())
        except Exception:
            return
        if seconds > 60:
            return
        setup = position.setup
        key = (setup.symbol, setup.timeframe, setup.direction)
        self._instant_stop_penalty_dates.setdefault(key, set()).add(self._trading_date(closed_at))

    def seed_instant_stops(self, connection) -> int:
        """Restore same-day instant-stop memory across Railway restarts."""
        try:
            rows = connection.execute(
                """
                SELECT symbol, timeframe, closed_at, fingerprint_json
                FROM trade_intelligence
                WHERE status = 'CLOSED' AND outcome_class = 'INSTANT_STOP'
                  AND closed_at IS NOT NULL
                ORDER BY closed_at DESC
                LIMIT 250
                """
            ).fetchall()
        except Exception:
            return 0

        seeded = 0
        for row in rows:
            try:
                fp = json.loads(row[3] or "{}")
            except (TypeError, ValueError):
                fp = {}
            direction = str(fp.get("direction") or "")
            if direction not in {"bullish", "bearish"}:
                continue
            try:
                closed_at = datetime.fromisoformat(str(row[2]).replace("Z", "+00:00"))
            except Exception:
                continue
            key = (str(row[0]), str(row[1]), direction)
            day = self._trading_date(closed_at)
            before = len(self._instant_stop_penalty_dates.get(key, set()))
            self._instant_stop_penalty_dates.setdefault(key, set()).add(day)
            if len(self._instant_stop_penalty_dates[key]) > before:
                seeded += 1
        return seeded

    def _build_setup(self, candles, context, entry_fvg, market_time):
        setup = super()._build_setup(candles, context, entry_fvg, market_time)
        if setup is None:
            return None

        attempts = list(setup.metadata.get("entry_candidates", []) or [])
        valid_attempts = []
        for attempt in attempts:
            if not attempt.get("valid") or attempt.get("entry") is None:
                continue
            retracement = self._retracement_fraction(context, float(attempt["entry"]))
            attempt["retracement_fraction"] = retracement
            attempt["entry_zone"] = self._entry_zone(retracement)
            attempt.setdefault("details", {})["operation67_retracement"] = retracement
            valid_attempts.append(attempt)

        standard = [
            attempt
            for attempt in valid_attempts
            if attempt.get("entry_zone") == "OTE_705_79"
        ]
        deep = [
            attempt
            for attempt in valid_attempts
            if attempt.get("entry_zone") == "DEEP_79_88"
        ]

        if standard:
            # The user's .79 line is the preferred deep OTE. When multiple
            # standard candidates are valid, choose the deepest clean fill.
            chosen = max(
                standard,
                key=lambda attempt: (
                    float(attempt.get("retracement_fraction") or 0.0),
                    float(attempt.get("risk_reward") or 0.0),
                ),
            )
            aggressive = False
            entry_risk_cap = 1.0
            checklist = None
        elif deep:
            chosen = min(
                deep,
                key=lambda attempt: float(attempt.get("retracement_fraction") or 99.0),
            )
            aggressive = False
            entry_risk_cap = 0.75
            checklist = None
        else:
            shallow = [
                attempt
                for attempt in valid_attempts
                if attempt.get("entry_zone") == "SHALLOW_AGGRESSIVE"
            ]
            if not shallow:
                return self._reject_setup(
                    context,
                    "Operation 6.7 found no valid entry inside the user's Fib template. Waiting for a fresh OTE/FVG candidate.",
                    market_time,
                )

            chosen = max(
                shallow,
                key=lambda attempt: (
                    float(attempt.get("retracement_fraction") or 0.0),
                    float(attempt.get("risk_reward") or 0.0),
                ),
            )
            checklist = self._aggressive_checklist(candles, context, entry_fvg)
            retracement = float(chosen.get("retracement_fraction") or 0.0)

            if self._penalty_active(context, market_time):
                return self._reject_setup(
                    context,
                    "Operation 6.7 instant-stop penalty is active for this symbol/timeframe/direction today; shallow entries are disabled and price must reach the .705-.79 OTE zone.",
                    market_time,
                )

            if not checklist["all_confirmed"]:
                failed = [
                    name
                    for name in (
                        "strong_displacement",
                        "liquidity_sweep_complete",
                        "micro_structure_shift",
                        "fvg_retest_holds",
                        "no_immediate_opposing_pressure",
                    )
                    if not checklist[name]
                ]
                return self._reject_setup(
                    context,
                    "Operation 6.7 blocked a shallow aggressive entry before .705; "
                    f"missing confirmation: {', '.join(failed)}. Waiting for deeper .705-.79 OTE.",
                    market_time,
                )

            aggressive = True
            entry_risk_cap = self.SHALLOW_RISK_CAP if retracement >= 0.618 else self.VERY_SHALLOW_RISK_CAP

        rr = float(chosen.get("risk_reward") or 0.0)
        tier, multiplier = self._risk_tier(rr)
        lifetime_cap = float(setup.metadata.get("lifetime_risk_cap", 1.0) or 1.0)
        multiplier = min(multiplier, lifetime_cap, entry_risk_cap)

        setup.entry_price = float(chosen["entry"])
        setup.stop_price = float(chosen["stop"])
        setup.target_price = float(chosen["target"])
        setup.risk_reward = rr

        entry_type = str(chosen.get("entry_type") or setup.metadata.get("entry_type") or "UNKNOWN")
        setup.metadata.update(
            {
                "entry_type": entry_type,
                "entry_rule": (
                    "user Fib .79 preferred OTE / .705-.79 standard OTE"
                    if chosen.get("entry_zone") == "OTE_705_79"
                    else "qualified shallow aggressive entry with full confirmation"
                    if aggressive
                    else "deep .79-.88 discount fallback"
                ),
                "entry_priority": ["USER_OTE_705_79", "DEEP_79_88", "SHALLOW_AGGRESSIVE_CONFIRMED"],
                "entry_candidates": attempts,
                "fib_profile": self._fib_profile(),
                "retracement_fraction": chosen.get("retracement_fraction"),
                "entry_zone": chosen.get("entry_zone"),
                "aggressive_entry": aggressive,
                "aggressive_confirmation": checklist,
                "instant_stop_penalty_active": self._penalty_active(context, market_time),
                "discount_rule": "User Fib template: 0/.50/.618/.705/.79/.88/1; standard OTE .705-.79 with .79 preferred",
                "risk_tier": tier,
                "risk_multiplier": multiplier,
                "entry_risk_cap": entry_risk_cap,
                "operation": 6.7,
            }
        )
        return setup

    def on_candle(self, symbol: str, timeframe: str, histories):
        setup = super().on_candle(symbol, timeframe, histories)
        if setup:
            diag = self.diagnostics.get((symbol, timeframe))
            if diag is not None:
                retracement = setup.metadata.get("retracement_fraction")
                zone = setup.metadata.get("entry_zone", "UNKNOWN")
                diag["retracement_fraction"] = retracement
                diag["entry_zone"] = zone
                diag["aggressive_entry"] = bool(setup.metadata.get("aggressive_entry"))
                if retracement is not None:
                    diag["note"] = (
                        f"Operation 6.7 {zone}: {float(retracement):.3f} Fib entry at "
                        f"{setup.risk_reward:.2f}R, max risk {float(setup.metadata.get('risk_multiplier', 1.0)):.0%}."
                    )
        return setup
