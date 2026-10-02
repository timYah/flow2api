FROM ghcr.io/astral-sh/uv:0.10.12 AS uv

FROM python:3.11-slim

COPY --from=uv /uv /uvx /bin/

WORKDIR /app

# 安装锁定的 Python 依赖。项目本身是从仓库根目录启动的，不需要安装成 wheel。
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY . .

EXPOSE 8000

CMD ["uv", "run", "--no-sync", "python", "main.py"]
