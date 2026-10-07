# Image de rendu vidéo Lumena.
# Les bibliothèques suivent la matrice Docker officielle de Remotion pour
# Chrome Headless Shell. Le navigateur Remotion lui-même reste versionné dans
# le projet (node_modules/.remotion) avec les dépendances npm épinglées.
FROM node:20.19.5-bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        ffmpeg \
        libnss3 \
        libdbus-1-3 \
        libatk1.0-0 \
        libgbm-dev \
        libasound2 \
        libxrandr2 \
        libxkbcommon-dev \
        libxfixes3 \
        libxcomposite1 \
        libxdamage1 \
        libatk-bridge2.0-0 \
        libpango-1.0-0 \
        libcairo2 \
        libcups2 \
        fonts-liberation \
        fonts-noto-color-emoji \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /work

RUN node --version && npm --version
