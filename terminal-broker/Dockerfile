# Terminal broker: one Open Terminal container per user (see docs/terminal/PHUONG-AN-TACH-TERMINAL.md).
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
 && useradd --uid 10001 --create-home broker \
 && mkdir -p /data && chown broker /data
COPY broker ./broker

USER broker
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)"
CMD ["uvicorn", "broker.app:app", "--host", "0.0.0.0", "--port", "8000", "--ws", "wsproto", "--ws-max-size", "67108864"]
