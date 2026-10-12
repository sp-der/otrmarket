# OTR Market — Pre-Live Evaluation Audit

- **Audit date:** 2026-10-12
- **Branch:** `claude/upbeat-goodall-rbekun` (base `main` @ `a963690`)
- **Scope:** the whole repository `sp-der/otrmarket`. I read the code directly; the code, not the docs, is treated as the source of truth.
- **Safety:** no orders were sent to any broker, simulator or prop account. Every execution test runs against an in-memory or temp-file SQLite queue with simulated broker events.

Each claim carries a confidence tag:

- **[Certain]** — read in code, reproduced by a test, or both.
- **[Likely]** — a strong inference.
- **[Guessing]** — filling a gap.

---

## 13. Final readiness decision (stated first)

# ❌ NO-GO

The blocking reasons are listed below. Each one alone is enough.

1. **[Certain] No live execution path exists.**
   - The NinjaTrader execution adapter refuses any account whose name does not start with `Sim` (`ninjatrader/OTRExecutionBridge.cs:242,317`).
   - It accepts only `SIM_BRIDGE` commands (`:287`), and only quantity = 1 (`:297`).
   - A prop-firm evaluation account cannot be traded at all. Connecting one today would do nothing. If someone "fixed" that by editing the adapter, the result would be an uncertified adapter.
2. **[Certain] The implemented risk model is not the intended one.** Production (Operation 8.1) uses:
   - $750 risk on A+ and $500 on A setups;
   - a $1,000 internal daily stop;
   - no maximum on trades per day;
   - minimum reward-to-risk of 1.20–1.50R;
   - pending orders that expire after 12 bars on 1m.

   The intended model is $250 / $100 risk, $750 daily drawdown, 2 trades per day, 1:3 R:R and 15 bars. I did **not** change strategy-layer risk, because that needs your decision (§3, §4).
3. **[Certain] There is no verifiable evidence of an edge.**
   - The repository contains no trade-level results.
   - The replay/backtest worker runs the **Operation 7.0** engine (`src/research/replay/worker.py:81`), not production 8.1. Any reported profitable replay therefore describes a different strategy.
4. **[Certain] Protective-order failure is detected but never remediated.**
   - If the stop is rejected, or the adapter restarts between the entry fill and the bracket submission, the position stays open and unprotected.
   - OTR blocks new entries, but nothing flattens the position.
5. **[Certain] Prop-firm compliance is UNVERIFIED.**
   - The code profile is named `LUCID_PRO_50K` (`src/risk/evaluation.py`) with 50K-style parameters.
   - No verified document names the actual provider, account or rule set.

---

## 1. Executive summary

**What was inspected:**

- the whole Python runtime: the `src.main_58` → `src.main_81` monkey-patch chain, about 25 kLOC;
- both NinjaScript bridges;
- the dashboard and FastAPI routes;
- the risk engine, the paper executor and the broker command queue;
- the research and replay stack;
- CI, Docker and Railway configuration;
- the 512 existing tests.

I did the execution and risk review myself. Strategy, risk, market-data and security/backtest areas were each covered by a parallel read-only analysis. I verified their key claims before relying on them.

**What works [Certain]:**

- Live execution is OFF by default (`PAPER`, disarmed, with two extra LIVE interlocks), and no dashboard route can arm it.
- The broker command queue is idempotent: `setup_id` is unique and event IDs are deduplicated.
- Command state transitions cannot go backwards (`state_machine80.py`).
- Reconciliation fails closed on any position or order mismatch, or a missing protective stop.
- The kill switch is persistent and survives restarts.
- Contract specs are correct: GC is $100/pt and MGC is $10/pt (`catalog.py:26-27`).
- Position size is always floored and never forced up to 1 contract.
- The core ICT primitives are directionally correct and do not repaint on closed candles:
  - swing detection is a 2/2 fractal;
  - FVG detection is the 3-candle test.

**What failed or is missing:** see §9. Count: **7 CRITICAL and 16 HIGH**. Of those I **fixed 3 CRITICAL and 8 HIGH** (some partially) and **left 12 open** (4 CRITICAL, 8 HIGH). Most open items need your decision (strategy or risk behaviour) or a NinjaTrader machine to test on.

**Overall:** NO-GO for a live evaluation. The recommended next stage is a supervised **SIM101 certification** of the patched adapter, followed by forward paper testing of whichever risk model you approve (§11–12).

---

## 2. Architecture overview (actual)

```
NinjaTrader (OTRMarketBridge.cs, Realtime-only ticks)
   └─HTTP POST /market/api/bridge/ticks  (X-OTR-Bridge-Key, hmac compare)
        └─ market_quotes (SQLite, received_at = NT time, ingested_at = server time)
             └─ engine loop src/main.py ninjatrader_collector (id order)
                  └─ CandleBuilder 1m/5m/15m/1h (tick-closed, UTC buckets)
                       └─ strategy families (ICT/OTE67, early-arm, reversal, continuation,
                          rejection-block, Gold momentum, 5m intrabar lane)
                            └─ OTRPipeline80: regime → arbiter → runner-up promotion
                                 └─ _quality_gate_81 (A/A+, dynamic 1.2–1.5R, zone edge)
                                      └─ EvaluationRiskGuard.decide (paper ledger)
                                           └─ register chain: guarded → _register_setup_81
                                              → _register_setup_72 → PaperExecutor
                                                   └─ ExecutionGateway.handle_approved_setup
                                                        └─ evaluate_intent (safety.py)
                                                             └─ execution_commands queue
NinjaTrader (OTRExecutionBridge.cs, SIM-only)
   ├─ POST snapshot (positions + OTR-named orders, every ~1s) → reconciliation verdict
   ├─ GET  commands (claims) → LIMIT entry, DAY TIF → on fill: OCO target + stop
   └─ POST events (ACK/WORKING/FILLED/CLOSED/REJECTED/CANCELLED)
Dashboard: FastAPI src.dashboard.server_81 (also supervises the engine process)
```

**Component status:**

- **Operational:** tick ingress, candle building, the strategy pipeline, the paper ledger, the dashboard, and the broker queue in PAPER mode.
- **Partial:** broker execution. It is SIM-only, 1 contract only, has no live adapter, and had no cancel path before this audit.
- **Legacy but required:** `main_58…main_80`. They are inherited through monkey-patching and are not safe to delete.
- **Non-authoritative sidecars:** Nautilus shadow/parity, the Vibe research sidecar, and Confluence Intelligence.
- **Over-complicated:** 20+ generations of runtime patches, each wrapping the last. Behaviour can only be determined by importing the full chain. This is itself a reliability risk.

---

## 3. Strategy verification (intended vs. implemented)

| Rule | Intended | Implemented (8.1) | Evidence |
|---|---|---|---|
| Execution TF | 1m, with 5m/15m/1h context | 1m/5m/15m/1h all execute. 1m context is 5m + 30m narrative; 15m/1h count only as score votes | `execution_quality.py:9-21` [Certain] |
| Liquidity sweep | Required | Last closed bar only, vs. the latest same-TF 2/2 swing. PDH/PDL and session pools are never triggers | `ict.py:285-315` [Certain] |
| Displacement | Required | Body ≥1.5×, range ≥1.3× the 20-bar average. The A/A+ grader needs ≥1.65× / 1.35×. No MSS/BOS required | `ict.py:318-362`, `execution_quality.py:172` [Certain] |
| FVG | Valid FVG | 3-candle logic correct. It need not come from the displacement candle | `ict.py:197-227` [Certain] |
| Entry | 50–79% retracement | Retracement is measured on the **single displacement candle**. 8.1 then moves entry to the **least-favourable edge** of the zone that still meets the R:R floor (e.g. a .79 entry at 3.39R becomes ≈.70 at 2.60R) | `execution_policy81.py:230-327` [Certain] |
| Stop | Invalidation / swing | Swept level, else the most recent swing, plus a 0.01% buffer | `flexible_confluence.py:257-276` [Certain] |
| Target / R:R | ≈1:3 | Most recent opposing swing. Floors: 1.20R (A+), 1.30R (A), 1.50R otherwise; 3R only for rejection-block | `execution_policy81.py:168-201` [Certain] |
| Post-loss rule | — | A ≥2.0R requirement after a loss is checked **before** the zone shift, so the final trade can be 1.2R | `session_consistency.py:177` [Certain] |
| Pending expiry | 15 bars | 1m 12 / 5m 8 / 15m 5 / 1h 3, counted from registration | `execution_policy81.py:19-24` [Certain] |
| Candle close | Closed candles only | True for 1m and for HTF context. **The 5m intrabar lane builds setups from the forming candle** (3 confirmations over 0.75 s) and can register pending orders | `main_65.py:52-81,255-321`, `main_80.py:118-142` [Certain] |
| Premium/discount | Gate | Score only (about −1.6 grade points) | `market_intelligence.py:77-109,424-434` [Certain] |
| SMT | Optional | Only NQ↔ES. Never used for GC | `ict.py:387-427` [Certain] |
| Sessions | Not specified (env says 09:30–13:00) | Trades about 23h/day. Only the 17–18 ET halt and the weekend are blocked. `OTR_SESSION_START/END` only labels a risk tier, which 8.1 sizing ignores | `main_58.py:38-81` [Certain] |
| Markets | Gold only | `OTR_ACTIVE_STRATEGY_SYMBOLS` defaults to GC. NQ/ES re-enable with one env var and would reach the gateway (now blocked by the broker allowlist, see §10) | `runtime/session.py:18` [Certain] |
| Lookahead | None | No repainting on closed bars (290 prefix tests, 0 mismatches). The intrabar lane is the exception | scratch probe [Certain] |

**Extra strategy families that can execute** (none are part of the intended spec):

- early-arm 5/6 pre-arm;
- 7.2H shallow-price promotion;
- REJECTION_BLOCK_10_10;
- MSS_REVERSAL;
- TREND_CONTINUATION_REARM;
- GOLD_MOMENTUM_PULLBACK_72R;
- runner-up promotion.

Implication [Likely]: production is a multi-family, low-R:R, around-the-clock Gold system, not the ICT 1:3 model described in the brief. **I did not change any of it.** That is a strategy decision for you.

---

## 4. Risk management audit

| Control | Status | Evidence |
|---|---|---|
| Contract specs (GC/MGC) | ✅ Verified | `catalog.py:26-27` [Certain] |
| Floor sizing, no forced minimum | ✅ Verified | `paper.py:75-79`, `live/sizing.py`; tests [Certain] |
| Fees / slippage buffer in sizing | ❌ Absent (configurable broker-ledger commission added, default 0) | [Certain] |
| Risk per trade = $250 / $100 | ❌ Implemented as $750 / $500. B+ never trades. Env can lower risk but cannot recreate the $250/$100 split | `execution_policy81.py:12-14,376-425` [Certain] |
| Daily loss (paper guard) | ⚠️ Realized P&L only, from the paper ledger. Day boundary was midnight ET → **fixed to 18:00 ET** | `evaluation.py` [Certain] |
| Daily loss including open/pending risk | ✅ **Added at the broker layer**: loss used + open risk + new risk must be ≤ limit | `safety.py`, `store.daily_execution_ledger` [Certain] |
| Max trades/day | ❌ Unlimited in the paper guard by default (`EVAL_MAX_TRADES_PER_DAY` unset). ✅ **Added at the broker layer, default 2** | [Certain] |
| Max concurrent positions | ✅ Paper: 1 per symbol + account. ✅ **Added at the broker layer** (active commands ≤ 1) | [Certain] |
| Lockout persists across restart | ✅ Re-derived from the DB on each decision (paper and broker) | tests [Certain] |
| Lockout bypass via `start-fresh-run` | ⚠️ **Paper** loss state resets on run rotation (cookie auth only). The **broker** ledger is not run-scoped, so it is immune | `server_81.py:389`, `run_archive81.py` [Certain] |
| VERIFY mode | ❌ Bypassed every governor yet still mirrored to the gateway → ✅ **broker now refuses while VERIFY is set** | `main_72.py:26-30` [Certain] |
| Max drawdown (MLL) | ⚠️ EOD-trailing on realized balance using the calendar date (now the CME date) | `evaluation.py:245-258` [Certain] |
| Overnight / news / consistency rules | ❌ None enforced | [Certain] |
| Emergency stop | ⚠️ Blocks new commands. ✅ now also cancels undelivered commands and resting unfilled entries. ❌ does not flatten positions | §5 [Certain] |
| Process-independent protection | ❌ Every limit lives in the Python process. No broker-side or NinjaTrader account loss limit is configured. **CRITICAL architectural risk** | [Certain] |

**Prop-firm rules: UNVERIFIED.**

Missing: provider; account size; platform/data connection; daily-loss definition (realized vs. intraday equity); trailing-drawdown mechanics; reset time; automation policy; news/consistency rules; commissions.

The `LUCID_PRO_50K` profile ($50k start, $3k target, $2k EOD trailing MLL, $1,200 daily loss) is a configuration, not a verified rule set.

**Worked example of the bug I fixed [Certain]:**

- Before the fix, a −$750 loss at 19:30 ET was forgotten at 00:30 ET, even though it is the same CME trading day.
- One CME day could therefore lose about $1,600, which exceeds a $1,200 firm daily limit.
- The regression test is `EvaluationTradingDayTests`.

---

## 5. NinjaTrader execution audit

**Integration method [Certain]:** HTTP polling. A NinjaScript *indicator* on a chart:

- polls `GET …/bridge/execution/commands` every ≥500 ms;
- submits through `Account.CreateOrder/Submit`;
- posts account snapshots and order events back to OTR.

It is not an ATM strategy or a NinjaScript Strategy.

| Lifecycle step | Status |
|---|---|
| 1. Data current | ❌ No freshness gate → ✅ added: server receipt time ≤10 s **and** exchange timestamp within 10 s of the wall clock (catches restart-backlog replay and clock/timezone faults) |
| 2. Signal from valid data | ⚠️ Non-finite/≤0 ticks were stored → ✅ now dropped. Spike filter, dedupe and late-tick handling still open |
| 3. Risk authorises | ✅ Paper guard + broker-layer limits (new) |
| 4. Quantity | ✅ Floor sizing. ⚠️ The adapter requires qty = 1, while paper sizes 2–40 MGC (paper P&L ≠ executable P&L) |
| 5. Submitted once | ✅ Unique setup_id and command_id, adapter-side dedup. ❌ Two pollers could both claim a command → ✅ fixed with a conditional claim |
| 6. ACK | ✅ Event recorded. Unacknowledged commands expire after the 45 s TTL |
| 7. Fill reconciled | ✅ Snapshot reconciliation by contract and signed quantity |
| 8–9. Stop and target | ⚠️ Submitted **only after** the entry fill, from an in-memory plan. Lost on adapter restart → "no retained plan" → **unprotected position** (OPEN). DAY TIF → ✅ changed to GTC (uncompiled) |
| 10. Position monitored | ✅ Snapshot about every 1 s. Missing stop → reconciliation fails and blocks entries |
| 11. Exit/cancel | ❌ No cancel protocol: a DAY limit entry outlived the paper setup → ✅ server cancel directives (entry expiry from the 8.1 pending lifetime, kill switch) + adapter `CancelUnfilledEntry` (uncompiled) |
| 12. Recorded | ✅ Execution events ledger. The broker-ledger daily P&L is now exposed in `/execution/status` |

**Abnormal scenarios:**

- **Covered by server-side tests:** rejection; partial fill; stop rejection; manual position; disconnect/stale reconciliation; duplicate events; concurrent signals; concurrent pollers; restart persistence; kill switch; paper mode.
- **Not reproducible here** (need a NinjaTrader machine, all UNVERIFIED): NinjaTrader restart with a working order; OCO behaviour on the actual connection; stop gap-through; contract rollover; market closure; lost ACK with a real broker.

**Reversal risk [Likely]:**

- Exits use `Sell` / `BuyToCover` with qty 1 in OCO.
- If the position is closed manually while the bracket remains, a `Sell` on a flat account can open a short. Reconciliation would then flag a mismatch.

**Emergency liquidation:** not implemented. Until it is certified, use NinjaTrader's *Flatten Everything* manually.

---

## 6. Testing results (commands actually run)

| Command | Result |
|---|---|
| `python -m unittest discover -s tests` (baseline, before changes) | **512 passed** |
| `python -m unittest discover -s tests` (after changes) | **552 passed, 0 failed** |
| `python -m unittest tests.test_prelive_audit_execution` | **40 passed** |
| `ruff check src scripts tests --select E9,F` | All checks passed |
| `python -m compileall -q src scripts tests` | OK |
| `bash -n run_all.sh run_dashboard.sh` | OK |
| `node --check` on every `src/dashboard/static/*.js` | OK |
| Nautilus smoke scripts (CI job) | **Not run** (pinned NautilusTrader not installed here) |
| NinjaScript compile of `OTRExecutionBridge.cs` | **Not run.** No NinjaTrader assemblies here, so the C# change is **uncompiled** |

During development, two of my own new tests failed: one had a wrong arithmetic expectation, one used a fixture clock that didn't match the gate. I fixed both tests; the gates were not loosened. Three existing tests were adjusted because they relied on the old unsafe behaviour:

- an NQ intent approved without data;
- dashboard auth open with an empty password (two tests).

**Phase-10 matrix coverage** (`tests/test_prelive_audit_execution.py`):

- valid Gold order;
- floor sizing / unfundable risk;
- risk above cap;
- invalid stop;
- wrong multiplier;
- unresolved contract;
- non-Gold symbol;
- PAPER default;
- paper mode never queues;
- kill switch (blocks, cancels, survives restart);
- stale data;
- backlog exchange-time;
- duplicate signal;
- simultaneous signals;
- daily loss limit;
- next-stop-out breach;
- profits don't enlarge the budget;
- trade cap;
- lockout after restart;
- 18:00 ET roll (broker ledger and paper guard);
- broker rejection;
- stop failure;
- unprotected fill;
- partial fill;
- manual position;
- disconnect without retry storm;
- entry-expiry cancel;
- concurrent pollers;
- the API delivering cancels while blocked;
- the status ledger;
- refused paper registration never mirrored;
- VERIFY interlock;
- bad ticks dropped;
- empty password fails closed.

The **"pending setup exceeds 15 bars"** case is not tested at 15. Production uses 12/8/5/3, which existing tests already cover. Changing it to 15 needs your approval.

---

## 7. Simulation performance

**[Certain] No metric can be verified.**

- The repository holds no trade-level dataset: `data/` is gitignored and git history shows no datasets.
- No OPERATION-*.md reports a win rate, profit factor or P&L.
- "Full Week Baseline" is a run label, not a result.

Total trades, win rate, average R, profit factor, drawdown, expectancy, consecutive losses, and per-session or per-setup statistics are therefore all **UNVERIFIED**.

**Methodological problems that make any existing numbers unreliable [Certain]:**

1. **Replay runs the 7.0 engine.** It has no 8.0 arbiter or regime, no 8.1 zone shift, floors or sizing, and different pending lifetimes (`worker.py:81`, `otr_replay.py:37`).
2. **The paper ledger fills optimistically:**
   - entry on touch at the exact price, ignoring bid/ask;
   - exits exactly at stop or target even through gaps;
   - no commission or fees.
3. **Losses are erased.** A tick that jumps through both entry and stop *invalidates* the order instead of recording fill-then-stop (`paper.py:290-305`).
4. **Win rates are inflated.** Counterfactual "WOULD_WIN" outcomes are scored without checking that the entry would have filled, and those outcomes can relax R:R floors (`main_61.py:283-296`).
5. **Market Replay feeds have gaps.** Ticks from failed bridge POSTs are dropped silently.

The research simulator (`src/research/execution/simulator.py`) is more realistic: 1-tick slippage, $3/RT costs, stop-first on ambiguity. It is not the engine behind the dashboard P&L.

**To validate:**

1. Export `paper_trades` and `decision_traces_80` from the Railway volume.
2. Re-run the **8.1** engine through the research simulator with costs and stop-first fills.
3. Separate the development weeks from untouched out-of-sample weeks.
4. Require ≥100 trades before inferring an edge.

---

## 8. Security and reliability findings

| Finding | Severity | Status |
|---|---|---|
| Empty `DASHBOARD_PASSWORD` turned auth **off** for every route (kill-switch reset, run rotation, all data) | CRITICAL | ✅ Fixed: fails closed unless `OTR_DASHBOARD_ALLOW_NO_PASSWORD=1` |
| Static, never-expiring session token (HMAC of a constant). Secret falls back to the password | HIGH | Open |
| No login rate limit. `compare_digest` on str raised a 500 on non-ASCII | MEDIUM | Bytes compare fixed. Rate limit open |
| UI relabelled "Paper Balance" as "Account Balance" and "PAPER" as "TEST" | HIGH | ✅ Fixed (relabelling removed) |
| Hard-coded "PAPER MODE" / `"mode":"paper"` badges won't change in SIM/LIVE | MEDIUM | Open |
| Bridge-key holder can inject ticks and fake broker snapshots | MEDIUM | Open (by design; protect the key, use HTTPS) |
| No committed secrets in tree or history | — | ✅ Verified |
| Live trading default OFF; no dashboard arming route | — | ✅ Verified |
| Vibe sidecar on by default on Railway, sharing the `/app/data` volume | MEDIUM [Guessing] | Open |
| `src/storage/database.py:12` ignores `OTR_DB_PATH` while `app.py:27` honours it, so the engine and dashboard can read different DBs | MEDIUM | Open |
| `market_quotes` grows unbounded while the engine is down. `candles` / `decision_traces_80` never pruned | MEDIUM | Open |
| No CME holiday calendar | MEDIUM | Open |
| Partial / gapped candles not flagged; late ticks enter HTF but not 1m | HIGH | Open |

---

## 9. Issues identified

Severity scale: **C** = CRITICAL, **H** = HIGH, **M** = MEDIUM.

**C1 — No live-account execution path** · Open (blocker)
- **Location:** `OTRExecutionBridge.cs:242,287,297,317`
- **Root cause:** phase-1 adapter is SIM-only and qty = 1.
- **Consequence:** cannot trade the evaluation account.
- **Fix:** proposed. A new, certified adapter revision with partial-fill bracket resizing.
- **Verification:** code read.

**C2 — Risk model mismatch** · Open (needs owner)
- **Location:** `execution_policy81.py:12-24,168-201,376-425`
- **Root cause:** 8.1 design choice.
- **Consequence:** 3× the intended risk; no trade cap; low R:R.
- **Fix:** proposed (§12). Not changed.
- **Verification:** code read and tests.

**C3 — Replay ≠ production; no trade-level evidence** · Open
- **Location:** `research/replay/worker.py:81`
- **Root cause:** replay pinned to `main_70`.
- **Consequence:** reported performance does not describe 8.1.
- **Fix:** proposed (§7).
- **Verification:** code read.

**C4 — Protective failure not remediated** · Open
- **Location:** `OTRExecutionBridge.cs:448,461`
- **Root cause:** brackets come from an in-memory plan after the fill; no flatten procedure.
- **Consequence:** an unprotected position after a stop rejection or adapter restart.
- **Fix:** proposed. Server re-supplies plans for active commands; certified flatten-on-failure.
- **Verification:** detection test `test_protective_stop_failure_blocks_and_charges_full_risk`.

**C5 — Dashboard auth fail-open** · ✅ Fixed
- **Location:** `src/dashboard/app.py:76`
- **Root cause:** `auth_required()` returned `bool(password)`.
- **Consequence:** public kill-switch reset and run rotation.
- **Fix:** applied (fails closed).
- **Verification:** `test_empty_dashboard_password_fails_closed`.

**C6 — Broker gateway had no exposure, daily-loss, trade-count, data-freshness or symbol checks, and mirrored paper-refused setups** · ✅ Fixed (server)
- **Location:** `live/safety.py:40-185`, `store.py:621-830`, `main_72.py:199`
- **Root cause:** the gateway trusted the paper ledger.
- **Consequence:** stacked broker exposure; trading on stale data; NQ/ES transmission if re-enabled; orders for setups paper refused.
- **Fix:** applied.
- **Verification:** `ExposureAndDailyLimitTests`, `DataFreshnessTests`, `RuntimeMirrorTests`.

**C7 — No cancel path; kill switch only blocked new commands** · ✅ Fixed server-side; adapter uncompiled
- **Location:** `api.py`, `store.cancel_directives`, `OTRExecutionBridge.cs` (`CancelUnfilledEntry`)
- **Root cause:** protocol had no cancel message.
- **Consequence:** a stale DAY entry could fill hours later.
- **Fix:** applied.
- **Verification:** `test_kill_switch_*`, `test_stale_working_entry_gets_cancel_directive`, `test_commands_endpoint_delivers_cancels_even_when_dispatch_blocked`.

**H1 — Paper daily-loss day = calendar midnight ET** · ✅ Fixed
- **Location:** `risk/evaluation.py:124`
- **Root cause:** `.astimezone(NY).date()`.
- **Consequence:** about −$1,600 in one CME day.
- **Fix:** applied: 18:00 ET trading day.
- **Verification:** `EvaluationTradingDayTests`.

**H2 — DAY TIF protective orders** · ✅ Fixed (uncompiled)
- **Location:** `OTRExecutionBridge.cs` `SubmitProtectiveBracket`
- **Consequence:** stop expires at session end.
- **Fix:** applied: GTC.
- **Verification:** none (needs NinjaTrader).

**H3 — VERIFY mode bypasses governors yet mirrored to the broker** · ✅ Fixed (broker side)
- **Location:** `main_72.py:26-30`, `safety.py:22-37`
- **Consequence:** unlimited broker risk.
- **Fix:** applied: `VERIFY_MODE_FORBIDDEN`.
- **Verification:** `VerifyModeInterlockTests`.

**H4 — Non-finite / ≤0 ticks stored** · ✅ Fixed (partial)
- **Location:** `app.py:260`, `bridge_ticks`
- **Consequence:** a 0 tick sets the candle low to 0 or books a full-R stop.
- **Fix:** applied: drop and count. Spike filter open.
- **Verification:** `test_bridge_drops_non_finite_and_non_positive_ticks`.

**H5 — Concurrent pollers double-claim** · ✅ Fixed
- **Location:** `store.py:319`
- **Fix:** applied: conditional UPDATE plus rowcount.
- **Verification:** `test_concurrent_pollers_cannot_both_claim_a_command`.

**H6 — Engine restart replays backlog into the gateway with a fresh TTL** · ✅ Fixed (broker side)
- **Location:** `main.py:270-318`
- **Fix:** applied: `MARKET_DATA_NOT_CURRENT`. Paper still trades the backlog.
- **Verification:** `test_backlog_replay_with_old_exchange_time_blocks_entries`.

**H7 — Bare `MGC` contract / contract flapping** · ✅ Fixed (partial)
- **Location:** `gateway.py:14-29`, `contracts.py:16-19`
- **Fix:** applied: `CONTRACT_UNRESOLVED`. Rollover pinning open.
- **Verification:** `test_unresolved_contract_expiry_is_blocked`.

**H8 — Paper → "Account" relabelling** · ✅ Fixed
- **Location:** `trade-history-cleanup.js`
- **Fix:** applied.
- **Verification:** `node --check`; manual read.

**H9 — 5m intrabar lane acts on the forming candle** · Open (strategy)
- **Location:** `main_65.py`, `main_80.py:118-142`
- **Fix:** proposed: disable for GC execution, or require the bar close.
- **Verification:** agent probe.

**H10 — Zone-edge entry and post-loss 2R bypass** · Open (strategy)
- **Location:** `execution_policy81.py:230-327`, `session_consistency.py:177`
- **Fix:** proposed.
- **Verification:** agent probe.

**H11 — Optimistic paper fills; fill-then-stop erased; no costs** · Open
- **Location:** `paper.py:265-372`
- **Fix:** proposed.
- **Verification:** code read.

**H12 — Static session token** · Open
- **Location:** `app.py:85-110`
- **Fix:** proposed: random, expiring server-side sessions.

**H13 — Paper loss state resets on `start-fresh-run`** · Open (paper); broker ledger immune
- **Location:** `server_81.py:389`
- **Fix:** proposed: refuse on a day with realized loss.

**H14 — Late ticks accepted in HTF not 1m; partial candles unflagged** · Open
- **Location:** `strategies/candles.py`
- **Fix:** proposed.

**H15 — All protection is process-dependent** · Open
- **Fix:** proposed: firm-side or NinjaTrader account loss limits, GTC brackets (done), external watchdog.

**H16 — Trades about 23h/day; session env vars only label tiers** · Open (needs owner)
- **Location:** `main_58.py:38-81`
- **Fix:** proposed.

**MEDIUM (open):**

- no holiday calendar or news filter;
- consistency rule not enforced;
- `EVAL_SESSION_PROFIT_CAP=1500` contradicts the "no profit ceiling" doc;
- counterfactual WOULD_WIN inflation;
- PD and SMT not enforced;
- `DB_PATH` mismatch;
- unbounded tables;
- no tick dedupe;
- manual *working orders* invisible to reconciliation (only OTR-named orders are reported);
- hard-coded PAPER badges;
- no login rate limit.

---

## 10. Changes made

**Source files:**

| File | Change | Behaviour impact |
|---|---|---|
| `src/execution/live/config.py` | New broker-layer limits: allowed symbols (GC), max active commands (1), max daily loss ($750), max trades/day (2), max data age (10 s), commission/RT (0), 18:00 ET roll | Only affects SIM_BRIDGE/LIVE when armed; PAPER unchanged |
| `src/execution/live/safety.py` | Gates: `SYMBOL_NOT_ALLOWED`, `CONTRACT_UNRESOLVED`, `CONTRACT_MULTIPLIER_MISMATCH`, `RISK_ARITHMETIC_MISMATCH`, `MARKET_DATA_STALE`, `MARKET_DATA_NOT_CURRENT`, `EXPOSURE_LIMIT`, `DAILY_TRADE_LIMIT`, `DAILY_LOSS_LIMIT`, `VERIFY_MODE_FORBIDDEN` | Broker commands blocked in more cases (fail closed) |
| `src/execution/live/store.py` | Conditional claim; `daily_execution_ledger`; `trading_day_start`; data-age lookups; `cancel_pending_commands`; `cancel_directives` | Same |
| `src/execution/live/sizing.py`, `gateway.py` | `entry_expires_at` = 8.1 pending lifetime, carried in the command payload | Broker entries now have a deadline |
| `src/execution/live/api.py` | Commands response carries `cancels` (also while blocked); kill switch cancels undelivered commands; status exposes `daily_ledger` and new limits | Additive protocol field; old adapters ignore it |
| `src/main_72.py` | Don't mirror setups whose paper registration isn't PENDING | Fewer broker commands |
| `src/risk/evaluation.py` | Paper guard trading day = 18:00 ET (CME) | **Paper behaviour change:** evening losses now count toward the correct day. Strictly more conservative |
| `src/dashboard/app.py` | Auth fails closed on empty password; bytes compare; drop non-finite/≤0 ticks | Local dev without a password needs `OTR_DASHBOARD_ALLOW_NO_PASSWORD=1` |
| `src/dashboard/static/trade-history-cleanup.js` | Removed paper → account/test relabelling | UI now says "Paper" where values are paper |
| `ninjatrader/OTRExecutionBridge.cs` | GTC brackets; honours `cancels` (unfilled entry orders only) | **Uncompiled**; must be compiled and SIM-certified |
| `.env.example` | Documents the new variables | — |

**Tests:**

- **Added:** `tests/test_prelive_audit_execution.py` (40 tests).
- **Adjusted:**
  - `tests/test_execution_live72.py`: the allow-path test now uses Gold with fresh data.
  - `tests/test_dashboard.py`, `tests/test_research_dashboard_phase4.py`: explicit no-password opt-out.

**Unchanged on purpose:** strategy detection, R:R floors, A+/A risk amounts, pending bars, session hours, market selection.

---

## 11. Remaining blockers before any live evaluation

1. **Decide the risk model.** Keep 8.1 ($750/$500, 1.2R, no cap) or move to the intended one ($250/$100, 3R, 2/day, $750 DD, 15 bars), then implement and test it in the strategy layer.
2. **Decide strategy scope:**
   - whether to disable the extra families and the 5m forming-candle lane for execution;
   - whether to keep the zone-edge entry shift;
   - which session window applies.
3. **Rebuild the evidence:**
   - point replay at `main_81`;
   - run it through the research simulator with costs and stop-first fills;
   - test out-of-sample;
   - require ≥100 trades.
4. **Compile the adapter and SIM-certify it** using the sequence in `ninjatrader/OPERATION-7.2-SETUP.md`, plus:
   - an entry-expiry cancel;
   - a kill-switch cancel;
   - a GTC bracket surviving the session close;
   - an adapter restart between entry fill and bracket.
5. **Implement and certify:**
   - bracket plan recovery after restart;
   - a flatten-on-protection-failure procedure;
   - live-account support with partial-fill bracket resizing.
6. **Verify the prop-firm rules in writing** (provider, account, daily-loss definition, trailing mechanics, reset time, automation and news policy), and configure firm-side or NinjaTrader account loss limits.
7. **Fix the dashboard:** expiring sessions; a login rate limit; a mode badge driven by `/execution/status`; broker positions and P&L shown separately from paper.

---

## 12. Recommended evaluation configuration (proposal — not activated)

This is for the eventual pilot, **after** §11 is closed. It does not change any current setting.

| Item | Proposal |
|---|---|
| Instrument | MGC only, front month pinned explicitly (`OTR_EXECUTION_ALLOWED_SYMBOLS=GC`), roll managed manually |
| Max position | `OTR_EXECUTION_MAX_MICROS=5` (start at 1 during certification) |
| Risk per trade | $100 for the first 20 live trades, then $250 (`OTR_EXECUTION_MAX_RISK_DOLLARS`, `EVAL_RISK_PER_TRADE`) |
| Max daily loss | $500 internal (`OTR_EXECUTION_MAX_DAILY_LOSS`), below the firm limit |
| Max trades/day | 2 (`OTR_EXECUTION_MAX_TRADES_PER_DAY=2`, `EVAL_MAX_TRADES_PER_DAY=2`) |
| Session | 08:30–11:30 ET only (requires implementing a hard session gate; today's vars only label tiers) |
| News | No new entries 15 min before and after tier-1 US releases (to be implemented); flat before FOMC |
| Overnight | Flat by 15:45 ET (to be implemented); no weekend holds |
| Emergency | Dashboard kill switch (blocks and cancels resting entries) plus manual NinjaTrader *Flatten Everything*; firm-side daily loss limit as the backstop |
| Monitoring | Human supervising every session; alert on `reconciliation.ok=false`, protection mismatch, `MARKET_DATA_*` blocks, any `REJECTED` event, bridge heartbeat > 15 s |
| Commission | Set `OTR_EXECUTION_COMMISSION_PER_CONTRACT_RT` to the real round-turn fee |
