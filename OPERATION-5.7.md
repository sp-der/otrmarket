# Operation 5.7 - Fifteen-bar setup development window

Operation 5.7 extends the default eight-bar setup-development expiration to fifteen bars.

- ICT `WAIT_DISPLACEMENT`: 15 bars
- ICT `WAIT_ENTRY_FVG`: 15 bars
- ICT `WAIT_QUALIFYING_FVG`: 15 bars
- ICT `WAIT_VALID_RR`: 15 bars
- Rejection Block `WAIT_DISPLACEMENT`: 15 bars

The initial ICT signal-search window remains 16 bars. Pending-order expiry, stale-FVG grading, stale pre-entry move cancellation and all risk protections remain unchanged, so the engine receives more time to build a valid setup without allowing an outdated order to fill.
