# OTR Market Operation 4.4 — Qualifying FVG Scanner

Operation 4.4 is a focused strategy/scanner cleanup on top of Operations 4.1–4.3.

## Why

Replay proved that OTR could correctly reach:

PD Array → Signal → Displacement → FVG

but a same-direction FVG outside the required 50–79% displacement retracement zone was still displayed as `WAIT_ENTRY_FVG`. That made the scanner look contradictory because the FVG step was already passed.

## Changes

- Adds `WAIT_QUALIFYING_FVG` after an FVG is detected outside the 50–79% zone.
- Scanner badge shows `OUTSIDE ZONE` instead of pretending no FVG exists.
- Keeps FVG progress latched while OTR scans subsequent candles for a better same-direction FVG.
- Does not reset the post-displacement timer when a rejected FVG appears, preventing immortal setups.
- Adds `WAIT_VALID_RR` when an FVG is inside the 50–79% zone but current swing structure / risk-reward does not produce a valid trade.
- Keeps retracement progress latched while looking for another valid entry candidate.
- Bumps dashboard static assets to `v=4.4` to force browsers/Railway clients to load the new scanner logic.

## Intended scanner flow

PD Array → Signal → Displacement → Entry FVG → 50–79% → Risk / Reward

Possible waiting states after displacement:

- `WAIT FVG`: no same-direction FVG yet.
- `OUTSIDE ZONE`: an FVG exists, but it is not inside the required 50–79% zone.
- `WAIT R:R`: a correctly positioned FVG exists, but current stop/target structure is not yet valid.
- `SETUP READY`: all six gates pass.

Live order execution remains disabled. This operation only improves strategy qualification and scanner clarity.
