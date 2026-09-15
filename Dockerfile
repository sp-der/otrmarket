FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DASHBOARD_HOST=0.0.0.0

# NautilusTrader is observational only, but production includes the pinned
# package so authenticated parity diagnostics can replay the same Gold data.
COPY requirements.txt requirements-nautilus.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir -r requirements-nautilus.txt

COPY . .
RUN chmod +x run_all.sh run_dashboard.sh

# Railway supplies PORT dynamically. 8000 remains the local/default port.
EXPOSE 8000

CMD ["bash", "run_all.sh"]
