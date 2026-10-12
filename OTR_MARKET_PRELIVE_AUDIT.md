# OTR Market — Pre-Live Evaluation Audit (revision 2)

- **Branch:** `claude/upbeat-goodall-rbekun` (base `main` @ `a963690`)
- **Revision 1:** 2026-10-12, commit `a208a26`.
- **Revision 2:** 2026-10-12. This revision applies the owner's risk and strategy decisions and adds an explicit data-clock policy. It also corrects two claims from revision 1 (§0).
- **Revision 2.1:** pre-PR cleanup, owner decisions D1 and D2 (§0a).
- **Safety:** no orders were sent to any broker, simulator or prop account. All execution tests use in-memory or temp-file SQLite with simulated broker events.

Confidence tags:

- **[Certain]** — read in code, reproduced by a test, or both.
- **[Likely]** — a strong inference.
- **[Guessing]** — filling a gap.

---

## 0. Owner decisions applied, and corrections to revision 1

**Owner philosophy:** *"Unlimited GOOD trades, limited bad risk."* That means:

- Gold only.
- One active Gold idea at a time.
- No daily trade-count cap and no post-win cooldown.
- Account-level daily loss protection.
- Operation 8.1 risk tiers preserved: A+ up to $750, A around $500.
- Dynamic R:R preserved: 1.xR where 8.1 quality logic permits it, uncapped high-R (3R, 5R, ~6.88R).

**Corrections to revision 1:**

1. **Withdrawn: "the implemented risk model is not the intended one."** Revision 1 compared 8.1 against the brief's outdated $250/$100, 2-trades, 1:3 assumptions. The owner confirms that the 8.1 tiers and dynamic R:R are intended. This is no longer a finding.
   - Revision 1 also added a broker-layer **2-trades/day cap**. It has been **removed**: the default is now 0 = unlimited.
2. **Narrowed: "reported replay performance describes a different strategy."**
   - The offline research replay worker does run Operation 7.0 [Certain].
   - However, the production run archives (e.g. "Full Week Baseline", `src/dashboard/server_81.py:79`) come from the **production 8.1 engine fed by NinjaTrader Market Replay** through the live bridge [Likely]. So the full-week replay most plausibly *is* 8.1 behaviour.
   - Its weakness is the optimistic paper fill model (§7). The trade rows live only in the Railway volume, so they are still unverified from the repo.
3. **Corrected: "gap-through losses are erased."**
   - When one tick jumps from above entry to beyond the stop, `paper.py` invalidates the order.
   - A real resting buy limit would fill at the gap price and then stop out at about the same price. The true outcome is roughly a scratch minus fees and slippage, not −1R.
   - It is still unrealistic, but smaller than revision 1 implied (§7).

## 0a. Revision 2.1 owner decisions (pre-PR cleanup)

**D1 — Broker per-trade risk ceiling inherits the strategy budget.**
- `OTR_EXECUTION_MAX_RISK_DOLLARS`, when unset, now inherits `EVAL_RISK_PER_TRADE`. If neither is set, it is **$750** (the old code default was $350).
- A valid Operation 8.1 A+ (~$750) plan is no longer silently cut to $350 during SIM certification.
- The broker layer can still be set **lower** explicitly. A lower cap shrinks quantity (e.g. $500 → 10 MGC at a 5pt stop); it does not reject the trade.
- Strategy-layer risk (8.1 tiers, `eval_risk81`) is unchanged.

**D2 — Session profit cap disabled by default.**
- `EVAL_SESSION_PROFIT_CAP` now defaults to **0 = disabled** (was $1,500). It stays configurable, and a positive value still produces `SESSION_PROFIT_LOCK`.
- **Defect found and fixed while doing this [Certain]:** a disabled cap previously computed `session_profit_remaining = $0`. The legacy session sizer `_setup_risk_66` (`src/main_66.py:31-62`) caps risk to `remaining / R`, so that would have sized every trade to **$0** rather than disabling the cap. A disabled cap now reports `session_profit_remaining = None` (no limit), which `_setup_risk_66` already treats as "do not cap". Covered by a regression test.
- The supervisor's legacy 7.2M repair explicitly never rewrites `EVAL_SESSION_PROFIT_CAP` (`server_72.py:56-60`), so the new default is effective.
- **Production impact on merge:** if Railway does not set `EVAL_SESSION_PROFIT_CAP`, the paper engine stops applying the $1,500 lock. That is the intended policy change. The Railway value is unverified; nothing was read or changed there.
- **Not changed (scope):** the research simulator's reference account profile (`src/research/execution/account.py:23`) still defaults to a $1,500 session cap. Its check (`:126`) has no `> 0` guard, so setting it to 0 there would lock every trade. Align it in the replay-parity follow-up (§9a).

---

## 13. Final readiness decision (stated first)

# ❌ NO-GO for a live / prop evaluation account

# ✅ Branch suitable for PR review as infrastructure hardening (see §11a)

Blocking reasons for live trading, each sufficient alone:

1. **[Certain] No live-account execution path.** The NinjaTrader adapter accepts only `Sim*` accounts, only `SIM_BRIDGE` commands, and only qty = 1 (`ninjatrader/OTRExecutionBridge.cs:242,287,297,317`). This is intentionally kept (owner Task 7).
2. **[Certain] Protective-order failure is detected but never remediated.** A stop rejection, or an adapter restart between the entry fill and the bracket submission, leaves the position open and unprotected. OTR blocks new entries but nothing flattens.
3. **[Certain] No verifiable edge in the repo.**
   - Production paper results exist only in the Railway DB, and their fill model is optimistic.
   - The offline replay worker runs 7.0.
4. **[Certain] Prop-firm rules are UNVERIFIED.**
   - `LUCID_PRO_50K` is only a profile label.
   - The 18:00 ET day boundary is an OTR convention, not a firm rule.
5. **[Certain] The NinjaScript changes are uncompiled.** No NinjaTrader assemblies are available in this environment.

---

## 1. Executive summary

**Counts:**

- **CRITICAL: 6.**
  - Open (3): no live path; protection failure unremediated; no verified edge.
  - Fixed (3): dashboard auth fail-open; broker gateway lacked exposure/loss/data checks; no cancel path.
- **HIGH: 18.**
  - Fixed: 11, some partially (including H17 and H18 in revision 2.1).
  - Open: 7. Most are strategy or owner decisions, or need a NinjaTrader machine.

**What works [Certain]:**

- Live execution is OFF by default, and no dashboard route can arm it.
- The broker command queue is idempotent, and command state cannot go backwards.
- Reconciliation fails closed on any position or order mismatch, or a missing stop.
- The kill switch persists across restarts.
- Contract specs are correct (GC $100/pt, MGC $10/pt).
- Sizing is floored to whole contracts and never forced up to 1.
- The core ICT primitives (2/2 swings, 3-candle FVG) are directionally correct and do not repaint on closed bars.
- The research simulator already implements conservative fills (§7).

**This revision's changes:**

- Broker trade-count cap removed (unlimited by default).
- Broker daily loss ceiling now inherits the paper guard's `EVAL_INTERNAL_DAILY_STOP`, so the two layers cannot silently disagree.
- **Explicit WALL / REPLAY data-clock policy**, plus feed-reorder and signal-backlog detection.
- Replay artifacts now record their true engine; manifests claiming a different engine are refused.
- The day-boundary disclaimer is documented.

---

## 2. Architecture overview (actual)

```
NinjaTrader OTRMarketBridge.cs (Realtime ticks only)
  → POST /market/api/bridge/ticks  (bridge key; non-finite/≤0 prices dropped)
  → market_quotes (received_at = NT event time, ingested_at = server time)
  → engine loop (src/main.py) → CandleBuilder 1m/5m/15m/1h (tick-closed)
  → strategy families → OTRPipeline80 (regime → arbiter → runner-up)
  → _quality_gate_81 (A/A+, dynamic R:R, zone edge)
  → EvaluationRiskGuard (paper ledger: daily stop, MLL, concurrency, streak)
  → register chain: 7.2T one-symbol guard → 8.1 registration clock
      → 7.2 broker mirror (only if paper status PENDING) → 6.4 preflight → PaperExecutor
  → ExecutionGateway → evaluate_intent (safety.py) → execution_commands
NinjaTrader OTRExecutionBridge.cs (SIM only)
  snapshot ↔ reconciliation | GET commands (+cancels) | POST events
```

The production engine is `src.main_81`, a monkey-patch chain from `main_58` through `main_80`. Behaviour is only determinable by importing the full chain.

---

## 3. Strategy verification (documented — not changed)

Revision 1 §3 findings stand as **observations**. Per the owner, none of them were changed in this branch:

- Execution on 1m/5m/15m/1h.
- Entry moved to the least-favourable zone edge that still meets the R:R floor.
- A post-loss ≥2.0R check evaluated before the zone shift (`session_consistency.py:177`).
- The 5m intrabar lane acting on the forming candle (`main_65.py`, `main_80.py:118-142`).
- Premium/discount and SMT are score-only (SMT is NQ/ES only).
- Trading runs about 23h/day.
- Extra families: early-arm, reversal, continuation, rejection-block, Gold momentum, runner-up promotion.

**R:R in force [Certain]:**

- Floors: A+ 1.20R, A 1.30R, otherwise 1.50R; rejection-block 3.0R (`execution_policy81.py:168-201`).
- **No reward cap** exists anywhere in the gateway or sizing. Regression tests show a ~6.88R plan is transmitted with its target unchanged, and a 1.2R A+ plan is not blocked by the broker layer.

**Owner hypothesis (post-win give-back): not acted on.** It needs sequence analysis on a larger sample than 8 trades.

**Session profit cap — resolved in revision 2.1 (§0a D2):**

- The paper guard's `EVAL_SESSION_PROFIT_CAP` previously defaulted to $1,500, an effective post-win stop. It now defaults to 0 = disabled and stays configurable.
- This matches "no session profit ceiling" (`OPERATION-8.1.md`) and the stated philosophy.

---

## 4. Risk management audit

| Control | Status |
|---|---|
| A+ ≈ $750 / A ≈ $500 (8.1) | ✅ Preserved, unchanged (`execution_policy81.py:12-14`). Env can only lower it |
| Whole-MGC floor sizing | ✅ Verified ($750 at a 5pt stop = 15 MGC; $500 at a 3.7pt stop = 13 MGC / $481) |
| Execution risk cap `OTR_EXECUTION_MAX_RISK_DOLLARS` | ✅ (revision 2.1) Unset → inherits `EVAL_RISK_PER_TRADE`, else $750. Explicit lower values are honoured and shrink quantity |
| Daily trade-count cap | ✅ **None by default** (broker `OTR_EXECUTION_MAX_TRADES_PER_DAY=0`; paper `EVAL_MAX_TRADES_PER_DAY=0`). Optional cap still works if set |
| One active Gold idea | ✅ Paper (one per symbol + `EVAL_MAX_CONCURRENT=1`) and broker (`OTR_EXECUTION_MAX_ACTIVE_COMMANDS=1`) |
| Daily loss — paper guard | ✅ Realized P&L, internal stop `EVAL_INTERNAL_DAILY_STOP` (code default $750, `.env.example` $1,000), 18:00 ET day |
| Daily loss — broker layer | ✅ Realized + unresolved-exposure loss + open risk + new risk ≤ ceiling. Ceiling = `OTR_EXECUTION_MAX_DAILY_LOSS`, else `EVAL_INTERNAL_DAILY_STOP`, else $750. Derived from the persisted broker ledger, so it survives restarts and run rotation |
| No post-win cooldown | ✅ None added. Test `test_no_cooldown_after_a_win` |
| Session profit lock | ✅ (revision 2.1) Disabled by default (`EVAL_SESSION_PROFIT_CAP=0`); a disabled cap never sizes risk to $0; a positive value still locks |
| MLL headroom | ✅ Paper guard, EOD-trailing on realized balance |
| VERIFY mode | ✅ Broker refuses while `OTR_TRADING_MODE=VERIFY` (all governors are off in VERIFY) |
| Run rotation (`start-fresh-run`) | ⚠️ Resets **paper** loss state. The broker ledger is immune |
| Overnight / news / consistency | ❌ Not enforced (owner/firm decision) |
| Process-independent protection | ❌ All limits live in the Python process; no firm-side or NinjaTrader account loss limit configured |

**Daily boundary [Certain]:** both layers use an **18:00 New York** trading day.

- **This is OTR's research/execution-day convention. It is NOT claimed to match any prop firm's reset or drawdown rules.**
- `LUCID_PRO_50K` is a label only. The provider's official rules must be verified and configured separately.

**Prop-firm compliance: UNVERIFIED.** Missing: provider, account, daily-loss definition, trailing mechanics, reset time, automation, news and consistency policies, commissions.

---

## 5. NinjaTrader execution audit

The integration is HTTP polling by a NinjaScript indicator that uses `Account.CreateOrder` / `Submit`.

| Step | Status |
|---|---|
| Data current | ✅ Explicit data-clock policy (§5a) |
| Risk authorises | ✅ Paper guard + broker layer |
| Submitted once | ✅ Unique setup/command IDs; atomic conditional claim (concurrent pollers tested) |
| Fill reconciled | ✅ Snapshot reconciliation by contract and signed quantity |
| Stop and target | ⚠️ Placed only after the entry fill, from an in-memory plan; lost on adapter restart (OPEN). GTC brackets (uncompiled) |
| Exit / cancel | ✅ Server cancel directives (kill switch; entry expiry in wall or replay time) + adapter `CancelUnfilledEntry` (uncompiled) |
| Emergency flatten | ❌ Not implemented; use NinjaTrader *Flatten Everything* manually |
| Variable quantity | ❌ Adapter rejects qty ≠ 1. Paper sizes 2–40 MGC, so paper P&L is not executable P&L yet |

### 5a. Data-clock policy (new in revision 2)

The policy is configured explicitly with `OTR_EXECUTION_DATA_CLOCK`. It is **never auto-detected**, and any unknown value means `WALL`.

| Check | WALL (LIVE, real-time SIM) | REPLAY (explicit historical Market Replay) |
|---|---|---|
| Quote received in the last `MAX_DATA_AGE` s (server clock) | Required → `MARKET_DATA_STALE` | Required (stalled or paused stream) |
| Feed not running backwards (newest event vs last 50) | Required, 10 s tolerance → `MARKET_DATA_REORDERED` | Required, `MAX_SIGNAL_LAG` event-second tolerance (replay rewind; tolerates multi-chart interleave at high replay speed) |
| Newest exchange time vs wall clock | Within 10 s → `MARKET_DATA_NOT_CURRENT` | **Not applied** (September data in October is fine) |
| Signal (8.1 registration event time) lag | Within `MAX_SIGNAL_LAG` s of wall clock → `SIGNAL_BACKLOG` | Within lag of the **replay stream head** → `REPLAY_SIGNAL_BACKLOG`; missing stamp → `REPLAY_SIGNAL_TIME_REQUIRED` |
| Allowed modes | PAPER / SIM_BRIDGE / LIVE | **SIM_BRIDGE only**; LIVE + REPLAY → `REPLAY_CLOCK_FORBIDDEN` |
| Entry-expiry cancel | Wall-clock deadline | Replay event-time deadline vs stream head |
| Daily ledger day attribution | Command creation time vs wall clock | Signal event time vs replay stream head |

**Known limitation [Certain]:** NinjaTrader Market Replay trades through the `Playback101` account. Both the server SIM check and the adapter accept only `Sim*` accounts. Replay-backed *broker* execution through the adapter therefore still needs a separately certified change. The server policy is ready; the account restriction was deliberately not loosened in this pass.

---

## 6. Testing results (commands actually run, revision 2)

| Command | Result |
|---|---|
| `python -m unittest discover -s tests` | **591 passed, 0 failed, 0 skipped** (revision 2: 581; revision 1: 552; baseline: 512) |
| `python -m unittest tests.test_prelive_audit_execution` | **79 passed** |
| `ruff check src scripts tests --select E9,F` | All checks passed |
| `python -m compileall -q src scripts tests` | OK |
| `bash -n run_all.sh run_dashboard.sh` | OK |
| `node --check` on all `src/dashboard/static/*.js` | OK |
| `OTR_NAUTILUS_SHADOW=1 python scripts/probe_nautilus_shadow.py` | exit 0 (nautilus_trader 1.231.0) |
| `python scripts/smoke_nautilus_gc_replay.py` | exit 0 |
| `python scripts/smoke_nautilus_execution_parity.py` | exit 0 |
| `python scripts/smoke_nautilus_parity_ledger.py` | exit 0 |
| NinjaScript compile | **Not run.** Not claimed |

**Tests added in revision 2** (`tests/test_prelive_audit_execution.py`):

- **Unlimited trades:**
  - default has no cap;
  - 3rd, 4th, 5th and 6th qualified trades are allowed;
  - no cooldown after a win;
  - loss capacity still blocks;
  - one active idea still enforced;
  - optional cap works when configured;
  - default config never trade-count blocks.
- **8.1 risk preservation:**
  - daily loss inherits `EVAL_INTERNAL_DAILY_STOP`;
  - an A+ $750 plan is transmittable (15 MGC);
  - an A $500 plan is floored to whole MGC;
  - a ~6.88R target is not reduced;
  - a 1.2R plan is not blocked.
- **Data clock:**
  - the policy is explicit and defaults to WALL;
  - fresh LIVE passes;
  - stale LIVE blocks;
  - a LIVE restart-backlog signal blocks;
  - historical data blocks without REPLAY;
  - historical data passes with explicit REPLAY;
  - replay backlog relative to the stream blocks;
  - a stalled replay blocks;
  - a replay rewind blocks;
  - multi-chart interleave under accelerated replay is tolerated;
  - REPLAY requires the registration stamp;
  - REPLAY is forbidden in LIVE;
  - replay entry expiry uses event time;
  - the replay ledger follows replay days;
  - a backwards feed blocks.
- **Revision 2.1 — broker risk ceiling:**
  - inherits `EVAL_RISK_PER_TRADE=750`;
  - an explicit $500 override wins;
  - fallback $750 with nothing set;
  - an A+ $750 intent is allowed with an env-derived config;
  - an explicit lower cap shrinks size rather than blocking.
- **Revision 2.1 — session profit cap:**
  - default 0 (dataclass and env);
  - a +$1,500 session does not stop the next setup (full $750 approved);
  - a disabled cap never zeroes risk in `_setup_risk_66`;
  - an explicit $1,500 cap still locks;
  - the env var is still configurable.
- **Replay provenance:**
  - the worker declares 7.0;
  - the runner refuses a mislabelled manifest before creating any run directory;
  - the git commit is detected rather than hard-coded.

Revision 1 tests are retained, including the kill switch, contract and multiplier integrity, rejection, partial fill, stop failure, manual position, concurrent pollers, auth fail-closed and bad-tick tests. Two of them were adapted:

- The trade-cap test now asserts that the default never caps.
- The backlog test now models a real backlog: all quotes old, rather than one old quote appended after a fresh one, which is now caught as a reorder.

---

## 7. Simulation performance and paper realism

**Verified metrics: none.** The repo holds no trade-level dataset.

**Paper engine (`src/execution/paper.py`), audited and not changed [Certain]:**

- Entries fill on touch at the exact limit price, using the last trade and ignoring bid/ask (`:325-330`).
- Exits fill exactly at the stop or target, even when price gaps through.
- No commission or fees.
- A single tick crossing the stop before touching entry → `INVALIDATED_BEFORE_ENTRY` (`:290-305`). The realistic outcome is fill-then-stop at about the gap price, i.e. a small loss of fees plus slippage (see §0.3).

**Recommendation: reuse the existing research simulator instead of duplicating fill logic.** `src/research/execution/simulator.py` already provides:

- `STOP_FIRST` ambiguity on stop-and-target in one bar (`:136-139`);
- entry-and-stop in the same candle booked as a loss under STOP_FIRST (`:130-135`);
- a stop gapped through filled at the open (`:142-143`);
- configurable slippage ticks, $2.50 commission + $0.50 fees per MGC round turn;
- a `BID_ASK` fill model.

To make research replay conservative:

1. **Engine:** run the 8.1 decision stack through `InLoopResearchExecutor` → `FuturesExecutionSimulator` (§9a), not through `PaperExecutor`.
2. **Defaults for evaluation runs:** `SLIPPAGE_MODEL`, ≥1 tick, `STOP_FIRST`, real MGC commission and fees.
3. **Add a `TRADE_THROUGH` entry option.** The fill would require price to trade one tick beyond the limit, removing optimistic queue-position assumptions. A favourable gap fills at the open (already implemented).
4. **Tick mode, target exits:** fill at the target, or the worse of target and last price, never better than the target.
5. **Tick mode, gap-through before entry:** book fill-then-stop at the tick price with costs. Do not invalidate.
6. **Keep the production `PaperExecutor` unchanged** until the owner decides. Then either reuse the simulator for paper accounting or label paper P&L "optimistic".

---

## 8. Security and reliability

| Finding | Severity | Status |
|---|---|---|
| Empty `DASHBOARD_PASSWORD` disabled auth for every route | CRITICAL | ✅ Fixed: fails closed unless `OTR_DASHBOARD_ALLOW_NO_PASSWORD=1` |
| Static, never-expiring session token | HIGH | Open |
| No login rate limit | MEDIUM | Open (bytes compare fixed) |
| UI relabelled paper values as "Account" values | HIGH | ✅ Fixed |
| Hard-coded "PAPER MODE" badges | MEDIUM | Open |
| Non-finite / ≤0 ticks stored | HIGH | ✅ Fixed (spike filter open) |
| `storage/database.py:12` ignores `OTR_DB_PATH`, while `app.py:27` honours it | MEDIUM | Open |
| No committed secrets | — | ✅ Verified |

---

## 9. Issues identified

### Critical

**C1 — No live-account execution path** · Open (kept by owner decision)
- **Location:** `OTRExecutionBridge.cs:242,287,297,317`
- **Consequence:** cannot trade an evaluation account.
- **Required before lifting:**
  - bracket recovery after restart;
  - emergency flatten;
  - variable-quantity certification;
  - NinjaTrader compile;
  - SIM101 lifecycle certification;
  - verified firm rules.

**C2 — Protective failure not remediated** · Open
- **Location:** `OTRExecutionBridge.cs:448,461`
- **Consequence:** unprotected position.
- **Fix (proposed):** server re-supplies plans for active commands; certified flatten-on-failure.

**C3 — No verifiable edge in the repo** · Open (reclassified, see §0.2)
- **Consequence:** live readiness unproven.
- **Fix (proposed):** §7 and §9a.

**C4 — Dashboard auth fail-open** · ✅ Fixed in revision 1.
- **Location:** `app.py:76`
- **Verification:** `test_empty_dashboard_password_fails_closed`

**C5 — Broker gateway had no exposure, loss, data or symbol checks, and mirrored paper-refused setups** · ✅ Fixed (revision 1, refined in revision 2).
- **Location:** `live/safety.py`, `store.py`, `main_72.py`
- **Verification:** exposure, loss, data-clock and mirror tests.

**C6 — No cancel path; kill switch didn't cancel** · ✅ Fixed server-side; adapter uncompiled.
- **Location:** `api.py`, `store.cancel_directives`, adapter
- **Verification:** kill-switch, expiry and API tests.

### High

**H1 — Paper daily day = calendar midnight ET** · ✅ Fixed (18:00 ET convention, not a firm rule).
- **Location:** `evaluation.py`

**H2 — DAY TIF protective orders** · ✅ GTC (uncompiled).

**H3 — VERIFY mode reached the broker** · ✅ Fixed.

**H4 — Bad ticks stored** · ✅ Fixed (partial).

**H5 — Concurrent poller double-claim** · ✅ Fixed.

**H6 — Restart backlog / historical data could reach the broker** · ✅ Fixed. Explicit WALL/REPLAY policy, signal-backlog and reorder detection (revision 2).

**H7 — Bare `MGC` contract** · ✅ `CONTRACT_UNRESOLVED`. Rollover pinning open.

**H8 — Paper → "Account" relabelling** · ✅ Fixed.

**H9 — Replay artifacts mislabelled** · ✅ Fixed (revision 2).
- **Root cause:** a hard-coded old git SHA in `scripts/otr_replay.py`, and no engine guard.
- **Fix:** commit detected; worker provenance recorded in the result; runner refuses mismatched `engine_version`.

**H10 — 5m intrabar lane acts on the forming candle** · Open (strategy, owner decision).

**H11 — Zone-edge entry; post-loss 2R check before the zone shift** · Open (strategy).

**H12 — Optimistic paper fills** · Open (recommendation in §7).

**H13 — Static session token** · Open.

**H14 — Paper loss state resets on run rotation** · Open (broker ledger immune).

**H15 — Late ticks accepted in HTF but not 1m; partial candles unflagged** · Open.

**H16 — All protection is process-dependent** · Open.

**H17 — `EVAL_SESSION_PROFIT_CAP=$1,500` acted as a post-win lock, contrary to the stated philosophy** · ✅ Fixed in revision 2.1 (default 0 = disabled; configurable; disabled no longer yields $0 sizing).

**H18 — Broker execution risk default of $350 would cut 8.1 A+ plans** · ✅ Fixed in revision 2.1 (inherits `EVAL_RISK_PER_TRADE`, fallback $750).

---

## 9a. Replay worker parity (Task 9 finding)

**Fact [Certain]:** `src/research/replay/worker.py` imports `src.main_70`. Production is `src.main_81`.

**Why switching it is not a safe one-line change [Certain]:**

1. **Instance-bound register chain.** Production wraps `runtime.paper.register_setup` on the *instance* with four layers:
   - 6.4 preflight / no-chase (`main_64.py:245`);
   - the 7.2 broker mirror (`main_72.py:221`);
   - the 7.2T one-symbol guard (`main_72t.py:112`);
   - the 8.1 registration clock (`main_81.py:383`).

   The worker replaces `runtime.paper` with `InLoopResearchExecutor`, which silently drops all four. The 8.1 registration-time pending lifetime would be lost, and the broker mirror must be explicitly excluded, not merely dropped.
2. **The 8.0/8.1 install happens in `main()`, not on import** (`main_80.py:144-170`). That covers the 4h backfill, the 4h context install, the single-symbol guard, persisted-state reconciliation, training capture and DB triggers, and `_install_pipeline_80` → `_install_pipeline_81`. Several of these write to the DB and must be curated for an isolated ledger.
3. **Different pending clocks.**
   - The in-loop executor expires from `setup.created_at` using 15/8/4/2 bars.
   - 8.1 expires from registration using 12/8/5/3 bars.
4. **Lanes and leakage.**
   - The intrabar lane runs only in tick mode.
   - The `x or datetime.now()` fallbacks (e.g. `registration_time81`) must be proven unreachable, or event-time fed, for wall-clock-free determinism.

**Recommended follow-up (a separate branch):**

1. **Shared installer.** Factor `main_80.main()` into `install_runtime80(isolated: bool)`, which production calls unchanged. In isolated mode it skips training capture and triggers, uses the run DB, and forces the gateway to a no-op.
2. **Wrapping executor.** Add `ReplayExecutor81`, which wraps `InLoopResearchExecutor` with the same guard → 8.1 stamp → 6.4 preflight chain, excluding the 7.2 mirror, and applies `PENDING_BARS_81` from registration time.
3. **Clock injection.** Feed `runtime.clock` from replay event time, and make the worker fail if any `datetime.now` fallback in the decision path is hit.
4. **Parity test.** Feed the same tick stream through the live engine (paper) and the 8.1 replay worker, and require identical selected setups and registrations. Record `engine_module=src.main_81` only once that passes.
5. **Provenance.** Until then the worker reports `production_parity: false`, and the runner refuses to label its output as anything but Operation 7.0 (implemented in this branch).

---

## 10. Changes made

**Revision 1** (commit `a208a26`, retained):

- Broker gateway hardening:
  - Gold allowlist;
  - contract / multiplier / risk-arithmetic integrity;
  - exposure limit;
  - daily loss limit on the 18:00 ET broker ledger;
  - VERIFY interlock;
  - atomic claim;
  - cancel directives;
  - the kill switch cancels pending commands.
- Paper refusals are never mirrored.
- Paper guard uses the 18:00 ET day.
- Dashboard auth fails closed.
- Bad ticks are dropped.
- Paper labels are no longer rewritten.
- Adapter: GTC brackets and cancel handling (uncompiled).

**Revision 2:**

| File | Change |
|---|---|
| `src/execution/live/config.py` | `max_trades_per_day` default **0 = unlimited** (env allows ≥0). `max_daily_loss_dollars` falls back to `EVAL_INTERNAL_DAILY_STOP`. New `data_clock` (WALL/REPLAY; unknown → WALL), `max_signal_lag_seconds`, `replay_clock` property |
| `src/execution/live/safety.py` | Trade cap applied only when > 0. New `_market_data_freshness` policy (stale, reorder, wall vs replay chronology, signal backlog). `REPLAY_CLOCK_FORBIDDEN` outside SIM_BRIDGE |
| `src/execution/live/store.py` | `recent_market_event_max`. Ledger attributes days by signal event time under REPLAY. Cancel directives judge entry expiry in replay event time under REPLAY. Day-boundary disclaimer |
| `src/execution/live/sizing.py` | Intent metadata carries `signal_event_at` (8.1 registration event time) and `entry_expires_event_at` |
| `src/execution/live/api.py` | Replay-aware cancels and ledger; status exposes `data_clock`, `max_signal_lag_seconds` |
| `src/research/replay/worker.py` | `ENGINE_MODULE` / `ENGINE_OPERATION` / `PRODUCTION_PARITY` constants; result includes `engine` provenance |
| `src/research/replay/runner.py` | `current_git_commit()`; refuses manifests whose `engine_version` ≠ the worker's engine |
| `scripts/otr_replay.py` | Records the detected commit (not a hard-coded old SHA) and the worker's engine label; prints provenance |
| `src/risk/evaluation.py` | Comment only: the 18:00 ET convention is not a firm rule |
| `.env.example` | Trade cap `0`; daily-loss override commented out (inherits `EVAL_INTERNAL_DAILY_STOP`); `OTR_EXECUTION_DATA_CLOCK=WALL`; signal lag; boundary disclaimer |
| `tests/test_prelive_audit_execution.py` | +29 tests (§6) |

**Revision 2.1:**

| File | Change |
|---|---|
| `src/execution/live/config.py` | `max_risk_dollars` default $750. `from_env` reads `OTR_EXECUTION_MAX_RISK_DOLLARS`, else `EVAL_RISK_PER_TRADE`, else $750 |
| `src/risk/evaluation.py` | `session_profit_cap` default 0 (disabled, env-configurable, negative clamped to 0). Disabled cap → `session_profit_remaining=None`; approval reason handles `None` |
| `src/dashboard/server.py` | Legacy rules panel shows "Disabled" when the cap is 0 |
| `.env.example` | Broker risk override commented out (inherits); `EVAL_SESSION_PROFIT_CAP=0` documented |
| `tests/test_prelive_audit_execution.py` | +10 tests |

**Not changed (strategy and risk):**

- 8.1 A+/A amounts, R:R floors, zone logic, pending bars, quality and expectancy logic.
- Strategy families, the intrabar lane, session hours, market selection.
- `EVAL_*` defaults other than `EVAL_SESSION_PROFIT_CAP` (revision 2.1, owner decision D2).
- The `PaperExecutor` fill model.
- The adapter's SIM-only restriction.
- Railway variables.

---

## 11. Remaining blockers before any live evaluation

1. Bracket-plan recovery after an adapter restart, plus a certified emergency flatten when protection cannot be established.
2. Variable-MGC-quantity adapter support with partial-fill bracket resizing.
3. NinjaScript compile, then SIM101 lifecycle certification:
   - entry, fill, OCO, cancel;
   - kill switch;
   - entry expiry;
   - GTC across the session close;
   - restarts;
   - manual position.
4. Verified evaluation-provider rules configured explicitly: daily loss definition, trailing drawdown, reset time, automation and news policy.
5. Strategy evidence: 8.1 replay parity (§9a) with conservative fills (§7), out-of-sample, ≥100 trades.
6. Owner decisions:
   - the intrabar lane (H10);
   - the zone-edge / post-loss interaction (H11);
   - session hours.

### 11a. PR readiness

**Ready for PR review** as an infrastructure-only hardening branch: tests green, no strategy or risk-tier change. Before merging, note:

- **Merging `main` deploys to Railway** (`DEPLOY-RAILWAY.md`). Confirm `DASHBOARD_PASSWORD` is set there. With it empty, the dashboard now locks instead of opening.
- **Production paper behaviour changes in two ways:**
  - the paper daily lock follows the 18:00 ET day, so evening losses count toward the correct day (more conservative);
  - unless Railway sets `EVAL_SESSION_PROFIT_CAP`, the $1,500 session profit lock no longer fires (owner decision D2).
- **No broker behaviour changes in PAPER mode.** All new broker gates apply only when SIM_BRIDGE or LIVE is armed.
- **The NinjaScript file is uncompiled.** Reviewers should treat the C# diff as pending certification.

---

## 12. Recommended controlled-pilot configuration (proposal — not activated)

This follows the owner's philosophy. Apply it only after §11 is closed.

| Item | Proposal |
|---|---|
| Instrument | MGC only, front month pinned (`OTR_EXECUTION_ALLOWED_SYMBOLS=GC`) |
| Concurrency | One active Gold idea (`OTR_EXECUTION_MAX_ACTIVE_COMMANDS=1`, `EVAL_MAX_CONCURRENT=1`) |
| Trades per day | Unlimited (`OTR_EXECUTION_MAX_TRADES_PER_DAY=0`, `EVAL_MAX_TRADES_PER_DAY=0`) |
| Risk per trade | 8.1 tiers, A+ ≤ $750 / A ≈ $500; broker ceiling inherits `EVAL_RISK_PER_TRADE`. Certification first at `OTR_EXECUTION_MAX_MICROS=1` |
| Session profit lock | Disabled (`EVAL_SESSION_PROFIT_CAP=0`) |
| Daily loss | `EVAL_INTERNAL_DAILY_STOP` set **below** the verified firm daily limit; broker inherits it |
| R:R | 8.1 dynamic floors; no reward cap |
| Data clock | `WALL` for live; `REPLAY` only for supervised SIM replay certification |
| Emergency | Kill switch (blocks and cancels resting entries) + manual *Flatten Everything* + firm-side loss limit |
| Monitoring | Supervised sessions; alert on reconciliation failure, protection mismatch, any `MARKET_DATA_*` / `*_BACKLOG` block, any `REJECTED` event, bridge heartbeat > 15 s |
| Commission | Set `OTR_EXECUTION_COMMISSION_PER_CONTRACT_RT` to the real round-turn cost |
