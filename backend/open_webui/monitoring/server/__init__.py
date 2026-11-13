"""NCS AI performance monitor: a separate web app, run in its own container.

    uvicorn open_webui.monitoring.server.app:app --host 0.0.0.0 --port 8090

It does not import the NCS AI application: it reads the perf_event table (and a few others) from PostgreSQL
read-only, asks NCS AI's internal /api/v1/monitor API for component status, and reads container
CPU/RAM from the Docker socket when it is mounted.
"""
