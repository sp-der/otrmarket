FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DASHBOARD_HOST=0.0.0.0

# Nautilus remains an optional/non-authoritative OTR subsystem at runtime, but
# the production image includes the pinned wheel so authenticated replay
# diagnostics can run against the same persistent Railway quote ledger.
COPY requirements.txt requirements-nautilus.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir -r requirements-nautilus.txt

COPY . .
RUN chmod +x run_all.sh run_dashboard.sh operation2_setup.sh operation3_setup.sh operation4_setup.sh 2>/dev/null || true

# Railway supplies PORT dynamically. 8000 remains the local/default port.
EXPOSE 8000

CMD ["bash", "run_all.sh"]
