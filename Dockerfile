FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DASHBOARD_HOST=0.0.0.0

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN chmod +x run_all.sh run_dashboard.sh operation2_setup.sh operation3_setup.sh operation4_setup.sh 2>/dev/null || true

# Railway supplies PORT dynamically. 8000 remains the local/default port.
EXPOSE 8000

CMD ["bash", "run_all.sh"]
