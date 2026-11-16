FROM apache/superset:6.1.0@sha256:16b50bbef6648912a79e3293d418fefa743ce555d36c5af6b85d859119ed7f88
USER root
RUN uv pip install --python /app/.venv/bin/python fastmcp==3.1.0 psycopg2-binary==2.9.11
COPY docker/superset /app/datapilot
USER superset
ENV SUPERSET_CONFIG_PATH=/app/datapilot/superset_config.py FLASK_APP=superset
