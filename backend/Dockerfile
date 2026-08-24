# 后端镜像：安装本包（src 布局）+ 携带迁移与种子脚本
FROM python:3.12-slim

WORKDIR /app

# 先装依赖再拷源码，利用层缓存
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .

# 迁移、种子与演示脚本随镜像携带（容器启动先 alembic upgrade head）
COPY alembic.ini ./
COPY migrations ./migrations
COPY scripts ./scripts

EXPOSE 8000
CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
