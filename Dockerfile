FROM node:24-alpine AS ui
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm ci --legacy-peer-deps
COPY frontend/ ./
RUN npm run build

FROM ghcr.io/astral-sh/uv:0.11.16 AS uv
FROM python:3.12-slim
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
COPY requirements.lock ./
RUN uv venv && uv pip sync requirements.lock
COPY backend/ ./backend/
COPY --from=ui /app/frontend/dist ./frontend/dist
ENV DATABASE_URL=sqlite:////app/data/roster.db
ENV COOKIE_SECURE=true
ENV PATH="/app/.venv/bin:$PATH"
RUN mkdir -p /app/data
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health')"]
CMD ["uv", "run", "--no-project", "--python", "/app/.venv/bin/python", "python", "-m", "uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
