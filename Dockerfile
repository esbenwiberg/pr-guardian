# ============================================================
# PR Guardian — Multi-stage Docker build
# ============================================================

# Stage 1: Build tools
FROM python:3.12-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Install gitleaks
RUN curl -sSfL https://github.com/gitleaks/gitleaks/releases/download/v8.21.2/gitleaks_8.21.2_linux_amd64.tar.gz \
    | tar xz -C /usr/local/bin gitleaks

# Install Python dependencies
COPY pyproject.toml .
RUN pip install --no-cache-dir --prefix=/install .

# Stage 2: Runtime
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    git && \
    rm -rf /var/lib/apt/lists/*

# Copy installed tools
COPY --from=builder /usr/local/bin/gitleaks /usr/local/bin/gitleaks

# Copy Python packages
COPY --from=builder /install /usr/local

# Install semgrep separately (large, benefits from layer caching)
RUN pip install --no-cache-dir semgrep

WORKDIR /app

# Copy application code
COPY src/ src/
COPY prompts/ prompts/
COPY pyproject.toml .

# Install the app itself (editable not needed in prod)
RUN pip install --no-cache-dir --no-deps .

# Non-root user
RUN useradd --create-home --shell /bin/bash guardian
USER guardian

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health')" || exit 1

ENTRYPOINT ["pr-guardian"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8000"]
