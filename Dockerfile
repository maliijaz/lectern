# Two stages: the web UI is built with Node, then copied into a Python image that serves
# both it and the API. One container, one port, one command.
#
# The image builds in two shapes, selected by EXTRAS:
#
#   full (default)  Docling, ChromaDB and embeddings. Reads PDFs and searches them, but
#                   pulls PyTorch, so the image is ~5 GB and needs well over 1 GB of RAM.
#   lite            EXTRAS="" — no PyTorch. A few hundred MB, starts in seconds, and fits
#                   a free hosting tier. Generation from a topic and every export format
#                   still work; document upload reports itself as unavailable rather than
#                   failing, because the app degrades capability by capability.
#
#   docker build --build-arg EXTRAS="" -t lectern:lite .

FROM node:22-slim AS web
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci --no-audit --no-fund || npm install --no-audit --no-fund
COPY frontend/ ./
RUN npm run build


FROM python:3.12-slim AS app

ARG EXTRAS="[ingest,media]"

# tesseract-ocr lets Docling read scanned PDFs; libgl and libglib are wheel runtime
# dependencies. A lite build needs none of them, so it only installs curl for the health
# check — which is most of why the lite image is small.
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && if [ -n "$EXTRAS" ]; then \
         apt-get install -y --no-install-recommends tesseract-ocr libgl1 libglib2.0-0; \
       fi \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    # Model weights land here; mount it as a volume to avoid re-downloading on every start.
    HF_HOME=/data/.cache/huggingface \
    LECTERN_DATA_DIR=/data \
    LECTERN_DATABASE_URL=sqlite+aiosqlite:////data/lectern.db \
    # Overridden by the platform where one assigns a port (Render, Fly, Cloud Run).
    PORT=8000

WORKDIR /app

# Dependencies first, so a source edit does not invalidate the (very large) install layer.
COPY backend/pyproject.toml ./backend/
RUN mkdir -p backend/app && touch backend/app/__init__.py \
    && pip install -e "./backend${EXTRAS}" \
    && rm -rf backend/app

COPY backend/ ./backend/
COPY --from=web /build/dist ./frontend/dist

RUN pip install -e ./backend && mkdir -p /data

VOLUME ["/data"]
EXPOSE 8000

# $PORT so the check follows the platform's assignment rather than assuming 8000.
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD curl -fsS "http://localhost:${PORT}/health" || exit 1

WORKDIR /app/backend
# Shell form so $PORT expands at runtime. Exec form would pass it as a literal string.
CMD uvicorn app.main:app --host 0.0.0.0 --port "${PORT}"
