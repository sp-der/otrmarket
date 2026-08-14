from __future__ import annotations

import os

import uvicorn


if __name__ == "__main__":
    uvicorn.run(
        "src.dashboard.app:app",
        host=os.getenv("DASHBOARD_HOST", "0.0.0.0"),
        port=int(os.getenv("DASHBOARD_PORT", "8000")),
        reload=False,
        proxy_headers=True,
        forwarded_allow_ips="*",
    )
