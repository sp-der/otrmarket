"""Fail-closed prospective eligibility for ranked fallback; no wall-clock input."""
from copy import deepcopy
from datetime import datetime, timezone, timedelta
import math

from src.execution.paper import _BAR_SECONDS, _preentry_target_progress
from src.otr8.execution_policy81 import HARD_NO_CHASE_PROGRESS, PENDING_BARS_81
from src.risk.geometry import validate_trade_geometry


def utc(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def check_runner_up81(setup, histories, runtime):
    """Return (status, reason, event_time, causal_histories).

    Candidate lifetime is the existing thesis-time paper expiry, never a newly
    stamped order-registration time. Missing observations cannot authorize fallback.
    """
    candles = list(histories.get((setup.symbol, setup.timeframe), []) or [])
    try:
        now = runtime.clock.event_time(setup.symbol)
    except (AttributeError, TypeError):
        now = None
    if now is None and candles:
        now = candles[-1].close_time
    if now is None:
        return 'RUNNER_UP_STALE', 'No observed market clock/candle for promotion.', None, {}
    now = utc(now)
    causal = {key: [bar for bar in bars if utc(bar.close_time) <= now] for key, bars in histories.items()}
    candles = causal.get((setup.symbol, setup.timeframe), [])
    seconds = _BAR_SECONDS.get(setup.timeframe)
    if seconds is None or not candles:
        return 'RUNNER_UP_STALE', 'Unsupported timeframe or no completed causal candle.', now, causal
    if not 0 <= (now - utc(candles[-1].close_time)).total_seconds() <= seconds:
        return 'RUNNER_UP_STALE', 'Execution candle is stale.', now, causal
    if now < utc(setup.created_at) or now > utc(setup.created_at) + timedelta(seconds=seconds * PENDING_BARS_81[setup.timeframe]):
        return 'RUNNER_UP_STALE', 'Candidate is future-dated or its thesis lifetime expired.', now, causal
    metadata = setup.metadata or {}
    expires = metadata.get('expires_at') or metadata.get('valid_until')
    if expires and now >= utc(expires):
        return 'RUNNER_UP_STALE', 'Explicit setup expiration reached.', now, causal
    if str(setup.status).upper() in {'INVALIDATED', 'EXPIRED', 'CLOSED'} or metadata.get('invalidated'):
        return 'RUNNER_UP_INVALIDATED', 'Thesis is no longer active.', now, causal
    prices = [float(candles[-1].close)]
    current_quote = getattr(runtime, "market_state", {}).get(setup.symbol, {}).get("price")
    if current_quote is not None:
        prices.append(float(current_quote))
    for bar in candles:
        # Never use the pre-signal range of the candle that formed the setup.
        if utc(bar.open_time) >= utc(setup.created_at) and utc(bar.close_time) > utc(setup.created_at):
            prices.extend([float(bar.low), float(bar.high)])
    if not all(math.isfinite(price) for price in prices):
        return 'RUNNER_UP_STALE', 'Non-finite market data.', now, causal
    bullish = setup.direction == 'bullish'
    if any(price <= setup.stop_price if bullish else price >= setup.stop_price for price in prices):
        return 'RUNNER_UP_INVALIDATED', 'Stop/thesis invalidation observed after signal.', now, causal
    if any(_preentry_target_progress(setup, price) >= HARD_NO_CHASE_PROGRESS for price in prices):
        return 'RUNNER_UP_ENTRY_PASSED', 'Move already exceeded the existing no-chase limit.', now, causal
    geometry = validate_trade_geometry(setup.symbol, setup.direction, setup.entry_price, setup.stop_price, setup.target_price)
    if not geometry.valid:
        return 'RUNNER_UP_INVALIDATED', geometry.reason, now, causal
    return None, 'Fresh causal market data and valid thesis/geometry.', now, causal


def session_probe(setup, event_time):
    probe = deepcopy(setup)
    probe.created_at = event_time
    return probe
