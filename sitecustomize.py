"""OTR process bootstrap.

Python imports sitecustomize automatically during interpreter startup when the
repository root is on sys.path. Install the Operation 8.0 SQLite concurrency
contract before dashboard/engine modules bind get_connection by value.
"""

try:
    from src.storage.database_concurrency80 import install

    install()
except Exception:
    # Never make Python itself unbootable because an optional bootstrap failed.
    # Production regression tests verify the patch is installed for OTR entrypoints.
    pass
