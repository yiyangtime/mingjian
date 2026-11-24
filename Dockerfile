# ── 第一阶段：构建前端 ─────────────────────────────────────────
FROM node:20-alpine AS web

WORKDIR /src/web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

# ── 第二阶段：运行后端 ─────────────────────────────────────────
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY pyproject.toml README.md LICENSE NOTICE DISCLAIMER.md ./
COPY mingjian/ ./mingjian/
COPY --from=web /src/web/dist ./web/dist

# 运行数据与日志挂载到卷，容器重建不丢
RUN useradd --create-home --uid 10001 mingjian \
    && mkdir -p data output logs \
    && chown -R mingjian:mingjian /app
USER mingjian

EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/api/health', timeout=3)"

CMD ["python", "-m", "mingjian", "serve", "--host", "0.0.0.0", "--port", "8765", "--no-open"]