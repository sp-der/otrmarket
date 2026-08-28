"""Operation 7.2 broker-execution plumbing.

The package is intentionally fail-closed. Production defaults to PAPER mode and
does not emit executable broker commands until SIM_BRIDGE is explicitly armed.
"""
