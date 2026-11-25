# Multi-stage lean Python 3.11 image for Vega Headless Backend
FROM python:3.11-slim

# Prevent Python from writing .pyc files and buffering stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=47632 \
    HEADLESS=1 \
    HOST=0.0.0.0

WORKDIR /app

# Install minimal runtime system utilities
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install Python requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Create non-root system user 'vega'
RUN groupadd -g 1000 vega && \
    useradd -u 1000 -g vega -m -s /bin/bash vega

# Copy application code into container
COPY --chown=vega:vega . .

# Ensure storage directories exist with non-root ownership
RUN mkdir -p /app/data && chown -R vega:vega /app

USER vega

EXPOSE 47632

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD curl -f http://127.0.0.1:47632/api/health || exit 1

CMD ["python", "vega_webview.py", "--headless"]
