# ==============================================================================
# Funbot Systematic Solana Momentum Engine — Multi-Stage Production Dockerfile
# ==============================================================================

# --- Stage 1: Build & Dependencies ---
FROM python:3.11-slim AS builder

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# --- Stage 2: Production Runtime ---
FROM python:3.11-slim AS runner

WORKDIR /app

# Create non-root unprivileged runtime user
RUN useradd -m -u 1001 funbot && \
    mkdir -p /app/data/telemetry /app/data/live /app/data/backtest && \
    chown -R funbot:funbot /app

# Copy installed wheels and binaries from builder
COPY --from=builder /root/.local /home/funbot/.local
ENV PATH=/home/funbot/.local/bin:$PATH

# Copy application code
COPY --chown=funbot:funbot . /app

USER funbot

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

EXPOSE 8501

ENTRYPOINT ["python", "main.py"]
CMD ["status"]
