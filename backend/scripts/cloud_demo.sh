#!/usr/bin/env bash
# M14 一键云端演示栈：任意一台装了 Python 的机器（VPS/本地）一条命令跑起
# 「前端 + API 同源单进程（:8010）+ shopverse 商城（:8001）」，纯 SQLite 免数据库，
# 可直接被 cloudflared 隧道暴露公网演示。
#
# 用法：
#   bash backend/scripts/cloud_demo.sh [商城公网URL]
#   商城公网URL：cloudflared 给 :8001 分配的 trycloudflare 地址。传入后发布回写的
#   商品页链接为公网绝对地址，访客点「在商城查看」直达；缺省按请求地址推导（仅本机可达）。
#
# 环境变量：
#   APP_PORT=8010          同源服务端口
#   SILICONFLOW_API_KEY    可选；注入则跑真 LLM，留空自动降级确定性 stub（零出网可演示）
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PY="$ROOT/backend/.venv/Scripts/python.exe"
APP_PORT="${APP_PORT:-8010}"

cd "$ROOT/backend"

echo "==> 1/4 前端构建产物（后端同源托管前端）"
if [ ! -d "$ROOT/frontend/dist" ]; then
  (cd "$ROOT/frontend" && npm run build)
fi

echo "==> 2/4 全新 SQLite 业务库 + 迁移 + 种子（租户/知识库）"
export DATABASE_URL="sqlite+aiosqlite:///./.localdata/cloud_demo.db"
rm -f .localdata/cloud_demo.db .localdata/checkpoints_cloud.db
PYTHONUTF8=1 "$PY" scripts/seed_mock_data.py >/dev/null
PYTHONUTF8=1 "$PY" scripts/seed_knowledge.py >/dev/null

echo "==> 3/4 启动 shopverse 商城 :8001"
(
  cd "$ROOT"
  MKT_URL="${1:-}"
  if [ -n "$MKT_URL" ]; then
    PUBLIC_BASE_URL="$MKT_URL" "$PY" mock-marketplace/server.py --demo &
  else
    "$PY" mock-marketplace/server.py --demo &
  fi
)

echo "==> 4/4 启动同源应用 :$APP_PORT（托管 API + 前端 SPA 回退）"
export FRONTEND_DIST_PATH="$ROOT/frontend/dist"
export CHECKPOINT_DB_PATH=".localdata/checkpoints_cloud.db"
export AUTO_APPROVE=true
export MOCK_MARKETPLACE_URL="http://127.0.0.1:8001"
exec "$PY" -m uvicorn app.api.main:app --host 0.0.0.0 --port "$APP_PORT"
