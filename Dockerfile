FROM node:24-bookworm-slim AS frontend-build

WORKDIR /app
RUN corepack enable
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml tsconfig.json tsconfig.app.json ./
COPY frontend ./frontend
RUN pnpm install --frozen-lockfile
RUN pnpm run build

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/backend

WORKDIR /app
COPY backend/requirements-runtime.txt ./backend/requirements-runtime.txt
RUN pip install --no-cache-dir -r backend/requirements-runtime.txt \
    && useradd --system --uid 10001 --create-home appuser
COPY --chown=appuser:appuser backend/app ./backend/app
COPY --chown=appuser:appuser --from=frontend-build /app/frontend/dist ./frontend/dist

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"

USER appuser
CMD ["uvicorn", "app.main:app", "--app-dir", "/app/backend", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
