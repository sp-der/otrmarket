FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN chmod +x run_all.sh run_dashboard.sh operation2_setup.sh

ENV DASHBOARD_HOST=0.0.0.0
ENV DASHBOARD_PORT=8000

EXPOSE 8000

CMD ["bash", "run_all.sh"]
