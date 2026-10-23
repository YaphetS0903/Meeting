FROM node:22-slim AS native-todo-cli
WORKDIR /tmp/wecom-cli
RUN npm install --omit=dev --no-save @wecom/cli@1.3.2 \
    && cp "node_modules/@wecom/cli-linux-$(node -p 'process.arch')/bin/wecom-cli" /wecom-cli \
    && chmod 0755 /wecom-cli

FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.10.12 /uv /usr/local/bin/uv
COPY --from=native-todo-cli /wecom-cli /usr/local/bin/wecom-cli
WORKDIR /app/backend
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY app ./app
COPY migrations ./migrations
RUN find ./migrations -type f -name "._*.py" -delete
COPY alembic.ini ./
RUN uv sync --frozen --no-dev && useradd --uid 10001 --create-home app \
    && mkdir -p /app/storage /home/app/.config/wecom \
    && chown -R app:app /app /home/app/.config
USER app
ENV PATH="/app/backend/.venv/bin:$PATH" PYTHONUNBUFFERED=1
CMD ["uvicorn","app.main:app","--host","0.0.0.0","--port","8000"]
