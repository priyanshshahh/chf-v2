# Project CHF operational scheduler image.
#
# Runs jobs/scheduler.py continuously as a non-root user. The scheduler shells
# out to the pipeline CLIs (main.py ...), the monitors (python -m monitoring.*)
# and the collectors (python -m providers.*), so the whole repo is copied in.
#
# Build:  docker build -f deploy/Dockerfile -t chf-scheduler .
# Run:    docker run --env-file .env -v "$PWD/data:/app/data" -v "$PWD/logs:/app/logs" chf-scheduler
FROM python:3.11-slim

# Faster, quieter, unbuffered logs (so docker logs show scheduler output live).
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=UTC

WORKDIR /app

# System deps: tzdata for correct UTC cron handling; build-essential only if a
# wheel needs compiling (kept minimal, removed after install).
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps first for better layer caching.
COPY requirements.txt ./
RUN python -m pip install --upgrade pip \
    && python -m pip install -r requirements.txt

# Copy the application.
COPY . .

# Non-root user; owns the data/ and logs/ dirs the scheduler writes to.
RUN useradd --create-home --uid 10001 chf \
    && mkdir -p /app/data /app/logs \
    && chown -R chf:chf /app
USER chf

# The scheduler is a long-running foreground process (BlockingScheduler).
ENTRYPOINT ["python", "-m", "jobs.scheduler"]
