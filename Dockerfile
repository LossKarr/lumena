# ============================================================
# LUMENA — Dockerfile (production)
# ============================================================
# Build :  docker build -t lumena .
# Run   :  docker run --env-file .env -p 8080:8080 lumena
# ============================================================

# ── Stage 1 : Build frontend ────────────────────────────────
FROM node:22-alpine AS frontend
WORKDIR /build
COPY web/package.json web/package-lock.json* ./
RUN npm ci --ignore-scripts
COPY web/ ./
RUN npm run build

# ── Stage 2 : Python runtime ────────────────────────────────
FROM python:3.12-slim AS runtime

# Dépendances système (Playwright, Tesseract, git)
RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr tesseract-ocr-fra \
        libglib2.0-0 libnss3 libnspr4 libdbus-1-3 libatk1.0-0 \
        libatk-bridge2.0-0 libcups2 libdrm2 libxkbcommon0 libxcomposite1 \
        libxdamage1 libxrandr2 libgbm1 libpango-1.0-0 libcairo2 \
        libasound2 libxshmfence1 \
        git curl \
    && rm -rf /var/lib/apt/lists/*

# Utilisateur non-root
RUN useradd -m -s /bin/bash lumena
WORKDIR /app

# Dépendances Python (cache layer)
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && playwright install chromium --with-deps 2>/dev/null || true

# Code source
COPY src/ ./src/
COPY web/ ./web/
COPY --from=frontend /build/dist/ ./web/dist/
COPY run_daemon.py run_telegram.py run_whatsapp.py pytest.ini ./

# Répertoire data persistant
RUN mkdir -p /app/data && chown -R lumena:lumena /app

# Métadonnées
LABEL maintainer="Losskarr" \
      description="Lumena — Agent IA autonome" \
      version="1.0.0"

EXPOSE 8080

USER lumena
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Healthcheck — endpoint /api/health de FastAPI
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -sf http://localhost:8080/api/health || exit 1

# Commande par défaut : serveur web (override possible)
CMD ["python", "-m", "uvicorn", "web.server:app", "--host", "0.0.0.0", "--port", "8080"]
