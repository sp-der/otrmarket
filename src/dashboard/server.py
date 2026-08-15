from __future__ import annotations

import os

import uvicorn


if __name__ == "__main__":
    port = int(os.getenv("PORT") or os.getenv("DASHBOARD_PORT", "8000"))
    uvicorn.run(
        "src.dashboard.app:app",
        host=os.getenv("DASHBOARD_HOST", "0.0.0.0"),
        port=port,
        reload=False,
        proxy_headers=True,
        forwarded_allow_ips="*",
    )
