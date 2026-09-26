# OTR Market — Independent Code & Strategy Review

Reviewed at commit `163c904` on branch `arena/01a0dd05-otrmarket`.
Scope: the whole repository (≈52k LOC, 336 files), with the deepest pass on the
money path — `src/execution/paper.py`, `src/otr8/execution_policy81.py`,
`src/risk/evaluation.py`, `src/otr8/pipeline.py`, `src/main*.py`,
`src/strategies/*`.

Baseline before this review: **502 tests passing**, `ruff --select E9,F` clean,
`compileall` clean. After the fixes below: **523 tests passing**, still clean.

---

## TL;DR — the four things that matter most

| # | Finding | Impact | Status |
|---|---------|--------|--------|
| 1 | **Paper sizing silently ran at 1 MGC contract** whenever `OTR_EXECUTION_MAX_MICROS` was unset, because it inherited `ExecutionConfig`'s deliberately-fail-safe default of `1` instead of `40`. | Replay P&L reported at ~1/25th of intended scale. Every expectancy, drawdown and "is this profitable?" number is affected. | **Fixed** + boot warning |
| 2 | **`EVAL_SESSION_PROFIT_CAP=1500` is a hard trading stop**, not a measurement objective — directly contradicting `OPERATION-8.1.md` ("No session profit ceiling"). | Caps a session at ~1.7 winning A+ trades. This is the single largest ceiling on profitability in the codebase. | Needs your call (see Q1) |
| 3 | **The paper ledger is gross of all costs.** Stops fill exactly at the stop price (even on gap-throughs) and no commission/fees are charged. | ~$16–$140 of unmodelled cost per round turn. Against a $3,000 eval target that is the whole target. | **Cost model added** (opt-in) |
| 4 | **`rr_decision81()` let positive counterfactual evidence drag a CHOP/regime-opposed floor from 1.50R back down to 1.20R**, violating the documented rule that evidence "cannot bypass … regime … gates". | The bot took its lowest-quality R:R in exactly the market conditions it was designed to avoid. | **Fixed** |

---

## P0 — Correctness & measurement integrity

### 1. Paper sizing inherited the live fail-safe default of 1 contract

`src/execution/paper.py` → `size_whole_contract()` resolved its cap through
`ExecutionConfig.from_env().max_micros`, which defaults to **`1`**
(`src/execution/live/config.py:37,57`). That `1` exists on purpose: OPERATION-7.2
wants an unarmed deployment to be physically incapable of sizing a real broker
order. Inheriting it into the *research* ledger meant:

```
requested $750, stop $3.00 (30 ticks) -> per-contract risk $30
  uncapped quantity = 25 contracts ($750 risk)
  cap = 1            -> quantity = 1  ($30 risk)
```

A `$750` A+ idea was booked as a `$30` trade. The ledger stayed internally
consistent — nothing crashed, no warning was logged — so the symptom looks like
"the strategy only makes $45 a trade" rather than "the config is wrong".
`EvaluationRiskGuard` then subtracted the *requested* `$750` from
`mll_headroom` as committed risk while only `$30` was actually at stake, so the
account guard was also over-conservative.

**Fix.** New `default_max_micros_cap()` with an explicit precedence order
(`src/execution/paper.py:53`):

1. `OTR_PAPER_MAX_MICROS` — paper/replay-only override
2. `OTR_EXECUTION_MAX_MICROS` — the operator ceiling the live path obeys
3. `EVAL_MAX_MICROS` — the evaluation-profile ceiling (default 40)
4. `DEFAULT_PAPER_MAX_MICROS = 40`

`ContractSizing` now also carries `cap_binding`, `uncapped_quantity` and
`cap_from_default`, and the startup envelope prints `PAPER_MAX_MICROS_CAP` and
warns when no ceiling is configured anywhere. Tests:
`SizingCapResolutionTests` (6 cases).

> **Action for you:** confirm what `OTR_EXECUTION_MAX_MICROS` is set to on the
> Railway deployment. If it was unset, every replay scorecard you have looked at
> so far needs to be re-run.

### 2. The $1,500 session profit cap is a hard stop, and it is undocumented

`src/risk/evaluation.py:310-313`:

```python
elif (status == "ACTIVE" and current_session
      and c.session_profit_cap > 0
      and session_pnl >= c.session_profit_cap):
    status = "SESSION_PROFIT_LOCK"
```

`decide()` then returns `allowed=False`, so OTR stops taking new risk for the
rest of the session bucket. `session_profit_cap` defaults to `1500.0`
(`src/risk/evaluation.py:75`) and **`EVAL_SESSION_PROFIT_CAP` was not in
`.env.example` at all**.

This contradicts the approved contract in `OPERATION-8.1.md`, which says:

> - Session objective: **$1,500** for measurement only.
> - No maximum number of trades.
> - **No session profit ceiling.**

…and the runtime's own boot banner in `src/main_81.py`, which announces
"no strategy trade quota and no profit ceiling".

The arithmetic is brutal: an A+ trade at the 1.20R floor risks `$750` to make
`$900`. Two winners = `$1,800` ≥ `$1,500` → locked. **Your upside is capped at
roughly 1.7 winning trades per session bucket** (ASIA / TOKYO / LONDON /
NEW_YORK), while the downside is governed by a `$1,000` daily stop. That is an
asymmetry working against you, and it is invisible in the docs.

Two related undocumented gates in the same file:

- `EVAL_MAX_CONSECUTIVE_LOSSES` defaults to **`3`** (`src/risk/evaluation.py:68`)
  → after three consecutive losses closed on the same trading day,
  `DAILY_LOCK` for the rest of the day. Not mentioned in `OPERATION-8.1.md`.
- `EVAL_INTERNAL_DAILY_STOP` defaults to **`750`** (`src/risk/evaluation.py:65`)
  while both `OPERATION-8.1.md` and `.env.example` specify **`$1,000`**. If the
  env var isn't set on the deployment, the daily stop is 25% tighter than
  specified.

**Status:** `.env.example` now documents all three with what they actually do.
I have **not** changed any default, because this is a risk-policy decision, not
a bug fix — see question 1 below.

### 3. The research ledger charges nothing and never slips

`src/execution/paper.py` `PaperExecutor.on_price()` (before this review):

```python
if stop_hit or target_hit:
    position.exit_price = setup.stop_price if stop_hit else setup.target_price
```

Consequences:

- A stop that is gapped through by a news print still books at the protected
  price. Gold gaps. Every gap is booked as a clean `-1R`.
- Zero commission, zero exchange/clearing/NFA fees.
- Fills are at `last`, never at bid/ask — the bridge captures `bid`/`ask`
  (`src/dashboard/app.py:275-282`) and they are stored in `market_quotes` but
  never used for fill logic.

Rough magnitude for MGC (tick = `$0.10` = `$1.00` P&L/contract):

| Stop width | Contracts @ $750 | Round-turn cost @ $1.60/contract | 1 tick stop slip | Total drag |
|---|---|---|---|---|
| $1.00 (10 tick) | 40 (capped) | $64 | $40 | **$104 on $400 actual risk** |
| $3.30 (33 tick) | 22 | $35 | $22 | **$57 on $726 actual risk** |
| $8.00 (80 tick) | 9 | $14 | $9 | **$23 on $720 actual risk** |

Against `EVAL_PROFIT_TARGET = $3,000`: 40 trades × ~$60 = **$2,400**, i.e. the
cost of doing business is ~80% of the entire evaluation target. Any strategy
that looks marginally profitable gross is likely a loser net.

**Fix.** A `PaperCostModel` (`src/execution/paper.py`) with two modes:

- `GROSS` (default, historical) — byte-identical to the old behaviour so
  existing runs stay comparable.
- `REALISTIC` — stop exits slip `OTR_PAPER_STOP_SLIPPAGE_TICKS` ticks **and**
  fill at the market price when it prints through the stop, plus per-contract
  round-turn commission and fees.

New persisted-on-position fields: `result_dollars_gross`, `commission_dollars`,
`fees_dollars`, `slippage_dollars`, `result_dollars_net`, `net_result_r`.
`result_dollars` becomes net when `REALISTIC` is enabled, so every existing
dashboard, funnel and eval-guard reader keeps working. Enable with:

```bash
OTR_PAPER_COST_MODEL=REALISTIC
OTR_PAPER_STOP_SLIPPAGE_TICKS=1        # MGC tick = $0.10
OTR_PAPER_ROUND_TURN_COMMISSION=1.24   # replace with your broker's numbers
OTR_PAPER_ROUND_TURN_FEES=0.36
```

The startup envelope now warns loudly whenever `GROSS` is active, so a gross
ledger can never again be mistaken for a net one. Tests: `CostModelTests`
(8 cases, including a gap-through-the-stop case).

> **Caveat:** these fields live on `PaperPosition` and are not yet written to
> `paper_trades` — that needs a schema migration touching both
> `src/storage/database.py` and `src/storage/database_concurrency80.py`. Until
> then, net P&L is visible in-process but not in the dashboard.

### 4. Positive counterfactual evidence could override the regime gate

`src/otr8/execution_policy81.py` `rr_decision81()`. The regime block raised the
floor to `1.50` for `CHOP`/`WARMUP` and for regime-opposed direction; the
evidence block ran afterwards and contained `floor = min(floor, 1.20)` for a
grade-A setup with `expectancy_r ≥ 0.20` and `win_rate ≥ 0.50`. Net effect:

```
regime = CHOP, grade = A, rr = 1.33, evidence = +0.55R over 40 samples
  before fix: floor = 1.20 -> ALLOWED
  after fix:  floor = 1.50 -> BLOCKED
```

Verified empirically before the fix. `OPERATION-8.1.md` is explicit that
"positive evidence cannot bypass setup quality, context, **regime**, exposure or
cooldown gates".

**Fix:** the relaxation is now gated on `not regime_tightened`. Negative
evidence (`expectancy_r <= 0`) still tightens to `1.50` in every regime, and an
aligned regime still allows the `1.30 → 1.20` relaxation exactly as designed.
Tests: `Operation81RegimeFloorAuthorityTests` (5 cases).

---

## P1 — Profitability & risk-of-ruin

### 5. No notional or margin guard on position size

`size_whole_contract()` is `floor(requested_risk / per_contract_risk)` capped at
the contract ceiling. Nothing looks at notional. A tight 10-tick stop on Gold
produces 40 MGC = **~$1.36M notional on a $50K evaluation account** (27:1). The
existing regression test documents 83 contracts on a 9-tick stop before the cap
was applied (`tests/…MaxMicrosCapTests`). Intraday margin on MGC is roughly
$1–2k, so 40 contracts approaches or exceeds the entire account's margin
capacity, and a 3-tick slip on 40 contracts is `$120` — 16% of a `$750` risk
budget from slippage alone.

**Recommendation:** add a notional cap (`max_notional / (price × point_value)`)
and/or a minimum stop width in ticks, and take the `min` of the three. I have
not implemented this because the right limit depends on your prop firm's margin
rules — tell me the numbers and I'll wire it in.

### 6. Counterfactual expectancy is computed from a negatively-selected sample

`counterfactual_expectancy81()` reads only rows from `counterfactual_setups` with
`outcome IN ('WOULD_WIN','WOULD_LOSE')` — i.e. **setups the bot rejected**. Using
the expectancy of rejected setups to tighten the floor for *accepted* setups is
conservative, but it is also structurally biased: those setups were rejected for
a reason, so their expectancy is not an unbiased estimate of the accepted
population's. Combined with the 20-sample threshold, a small run of bad luck
among rejects can tighten the live floor to 1.50R and starve the A-tier.

**Recommendation:** track a matched control sample of *accepted* setups too, and
either widen the gate to ~40 samples or move to a shrinkage estimate
(expectancy pulled toward the population mean by sample size).

### 7. Operation 8.2's LightGBM is badly underpowered

`src/otr8/confluence_intelligence/model.py`: `MIN_TRAINING_SAMPLES = 50` and
`PROMOTION_SAMPLES = 100`, against 14 features (`FEATURE_KEYS` plus 5 more in
`build_feature_row`). At ~7 samples per feature you are fitting noise; the
chronological split is correct anti-leakage hygiene, but it cannot rescue that
ratio. With a ~30–40% base rate on three binary targets, 100 samples gives a
±10pp confidence interval at best.

The anti-leakage discipline itself is good and worth protecting: `FORBIDDEN_KEYS`
is an explicit, testable denylist, features are a pure projection of pre-trade
metadata, and the module is correctly marked shadow-only. **Recommendation:**
raise `MIN_TRAINING_SAMPLES` to ~300 and `PROMOTION_SAMPLES` to ~1,000, or cut
the feature set to 4–5 and report out-of-sample metrics with confidence
intervals before anyone acts on a prediction.

### 8. Undocumented throttles compound into a very low trade count

Individually reasonable, collectively restrictive:

| Gate | Value | Source |
|---|---|---|
| `EVAL_MAX_CONCURRENT` | 1 position | `evaluation.py` |
| Same-symbol cooldown after a loss | 60 replay-min | `main_multi.py:_same_symbol_cooldown` |
| Same-symbol cooldown after a win | 20 replay-min | same |
| Global cooldown after **any** loss | 30 replay-min | `_global_loss_cooldown` |
| Consecutive-loss breaker | 3/day | `evaluation.py` |
| Session profit cap | $1,500/session | `evaluation.py` |
| Pending lifetimes | 12/8/5/3 bars | `execution_policy81.PENDING_BARS_81` |

On a 5m chart the post-loss cooldown alone is 12 bars. If you are seeing single
digit trade counts per week, this table is why — the strategy layer is likely
not the binding constraint.

---

## P2 — Data, execution and hygiene

### 9. No idempotency or ordering guard on tick ingest

`POST /market/api/bridge/ticks` (`src/dashboard/app.py:261`) inserts up to 5,000
rows per call with no dedupe key. An HTTP retry from NinjaTrader after a timeout
re-inserts the same ticks, duplicating prices into `CandleBuilder` and
potentially manufacturing phantom fills. `CandleBuilder.update()` silently drops
out-of-order ticks (`bucket_start < current["open_time"]` hits no branch), so a
late-arriving tick is discarded rather than merged.

**Recommendation:** include a monotonic per-symbol sequence number from
Ninjatrader and `INSERT OR IGNORE` on `(source, symbol, exchange_time, seq)`.

### 10. Documentation drift

- `README.md` says "Staging gate passed **76/76 tests**"; the suite is now 523.
- `OPERATION-8.1.md` omits the session profit cap and consecutive-loss breaker
  entirely, and states a $1,000 daily stop that the code defaults to $750.
- `.env.example` omitted `EVAL_SESSION_PROFIT_CAP` and `EVAL_MAX_CONSECUTIVE_LOSSES`
  — both now documented.
- `OPERATION-8.1.md` says "**READY FOR PRODUCTION REPLAY**" while the risk
  envelope is materially different from the documented one.

### 11. Minor

- `EvaluationRiskGuard._rows()` checks `PRAGMA table_info(paper_trades)` but
  queries `active_table(connection, 'paper_trades')` — correct today because the
  view mirrors the table, but fragile if the view ever narrows.
- `counterfactual_expectancy81()` wraps its whole query in `except Exception:
  return {...}` — a schema typo silently disables the evidence path forever.
- `except (TypeError, ValueError, json.JSONDecodeError)` is redundant
  (`JSONDecodeError` subclasses `ValueError`); harmless.
- `prepare_execution_zone81()` mutates `setup.entry_price/stop/target` inside the
  quality gate, so candidates that are later blocked by the arbiter keep the
  rewritten (most-conservative) geometry — which then feeds the counterfactual
  sample. Conservative, but worth knowing.
- `src/execution/live/sizing.py` has no commission/slippage either; the live
  intent and the research ledger will not agree on P&L until both model costs.

---

## What is genuinely good

Worth saying explicitly, because most of this is above the standard I'd expect:

- **The ingest loop is correctly ordered against look-ahead.**
  `process_price()` (`src/main.py`) runs `paper.on_price(...)` *before*
  `candles.update(...)` and `evaluate_strategy(...)`, so a setup discovered on a
  candle close can never fill on the same tick. This is the #1 place backtests
  cheat and you have it right.
- **Time filtering is disciplined.** `_history()` / `_history_at_or_before()`
  filter `close_time <= setup.created_at` in `market_intelligence.py`,
  `execution_quality.py` and `otr8/regime.py`.
- **Fail-closed execution kernel.** Idempotent `command_id`/`event_id`, sticky
  kill switch, reconciliation gate, and no dashboard route that can arm live
  trading (OPERATION-7.2).
- **Run scoping / research integrity.** `active_table()`, per-run accounting
  versions, and a reset token that refuses to run with an open position.
- **8.2 anti-leakage design.** Explicit `FORBIDDEN_KEYS` denylist, chronological
  split, shadow-only by construction.
- **Tick retention that respects the consumer.** `prune_market_quotes()` never
  deletes rows the collector hasn't acked via `last_ninjatrader_quote_id`.
- **Regression discipline.** 523 tests, CI static hygiene, `pip check`.

---

## Recommended sequence

1. **Verify the deployment env.** Confirm `OTR_EXECUTION_MAX_MICROS`,
   `EVAL_INTERNAL_DAILY_STOP`, `EVAL_SESSION_PROFIT_CAP`,
   `EVAL_MAX_CONSECUTIVE_LOSSES`, `EVAL_RISK_PER_TRADE` on Railway. If the
   contract cap was unset, discard every replay scorecard to date.
2. **Decide the session profit cap** (question 1 below). This is the biggest
   single lever on P&L in the repo.
3. **Turn on `OTR_PAPER_COST_MODEL=REALISTIC`** with your real broker numbers,
   then re-run a replay and compare gross vs net. Do not trust any gross number.
4. **Add the notional/margin guard** (finding 5) before raising the contract cap
   past ~20.
5. **Migrate the net-P&L columns into `paper_trades`** so the dashboard reports
   net, not gross.
6. **Only then** tune the strategy layer. With the measurement layer wrong, any
   strategy tuning is fitting to noise.

---

## Changes made in this review

| File | Change |
|---|---|
| `src/execution/paper.py` | `default_max_micros_cap()` / `cap_from_default()` with documented precedence; `ContractSizing.cap_binding` / `uncapped_quantity` / `cap_from_default`; `PaperCostModel` (GROSS/REALISTIC) with gap-through stop fills; new P&L attribution fields |
| `src/otr8/execution_policy81.py` | `rr_decision81()` — regime tightening is now authoritative over positive counterfactual evidence |
| `src/dashboard/server_81.py` | Startup envelope reports `PAPER_MAX_MICROS_CAP` and `PAPER_COST_MODEL`; warns on an unconfigured cap and on a gross ledger |
| `.env.example` | Documents `OTR_PAPER_MAX_MICROS`, `OTR_PAPER_COST_MODEL` (+ tuning), `EVAL_SESSION_PROFIT_CAP`, `EVAL_MAX_CONSECUTIVE_LOSSES`; clarifies the `OTR_EXECUTION_MAX_MICROS` default |
| `tests/test_operation81.py` | `Operation81RegimeFloorAuthorityTests` (5) |
| `tests/test_operation81_whole_mgc_accounting.py` | `SizingCapResolutionTests` (6), `CostModelTests` (8) |
| `tests/test_operation81_safety_patch.py` | Updated for the new envelope fields + 2 new warning tests |

Verification: `523 tests … OK`, `ruff check src scripts tests --select E9,F` clean,
`python -m compileall -q src scripts tests` clean.
