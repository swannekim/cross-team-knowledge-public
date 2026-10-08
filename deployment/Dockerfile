FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PORT=8080 KX_CONTAINER_DEPLOYMENT=1
COPY deployment/requirements-live.txt .
RUN pip install --no-cache-dir -r requirements-live.txt \
    && useradd --uid 10001 --create-home broker \
    && mkdir -p /app/state && chown 10001:10001 /app/state && chmod 700 /app/state
COPY app/common/ ./app/common/
COPY app/arch_b_broker/ ./app/arch_b_broker/
COPY deployment/runtime/ ./runtime/
WORKDIR /app/app
USER 10001
EXPOSE 8080
CMD ["python", "-m", "arch_b_broker.live"]
