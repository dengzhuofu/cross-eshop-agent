#!/usr/bin/env bash
# 一键 reset & replay（v1.4 M8 验收「一键 reset & replay」）。
# 前提：本机 PG 已起（bash backend/scripts/dev_postgres.sh），后端已在 :8000 运行。
# 用法：bash scripts/reset_and_replay.sh ["选题文案"]
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="$ROOT/backend/.venv/Scripts/python.exe"
TENANT="t_demo_acme"
IDEA="${1:-可折叠床底收纳箱}"

cd "$ROOT/backend"

echo "==> 1/4 迁移到最新 schema"
PYTHONUTF8=1 "$PY" -m alembic upgrade head

echo "==> 2/4 重置演示数据（租户 + 种子）"
PYTHONUTF8=1 "$PY" scripts/reset_demo.py
PYTHONUTF8=1 "$PY" scripts/seed_mock_data.py
PYTHONUTF8=1 "$PY" scripts/seed_knowledge.py

echo "==> 3/4 发起新工作流：「$IDEA」"
BODY_FILE="$(mktemp)"
PYTHONUTF8=1 "$PY" -c "
import json, sys
open(sys.argv[1], 'w', encoding='utf-8').write(json.dumps(
    {'product_idea': sys.argv[2], 'marketplaces': ['amazon', 'tiktok_shop']},
    ensure_ascii=False))
" "$BODY_FILE" "$IDEA"
WF=$(curl -sf -X POST http://127.0.0.1:8000/api/v1/workflows \
  -H "Content-Type: application/json" -H "X-Tenant-Id: $TENANT" \
  --data-binary @"$BODY_FILE")
rm -f "$BODY_FILE"
ID="$(echo "$WF" | PYTHONUTF8=1 "$PY" -c "import sys,json; print(json.load(sys.stdin)['id'])")"
echo "    workflow = $ID"

echo "==> 4/4 等待终态"
for _ in $(seq 1 60); do
  sleep 3
  ST=$(curl -sf -H "X-Tenant-Id: $TENANT" "http://127.0.0.1:8000/api/v1/workflows/$ID" \
    | PYTHONUTF8=1 "$PY" -c "import sys,json; print(json.load(sys.stdin)['status'])")
  echo "    status: $ST"
  case "$ST" in
    completed|failed|blocked|cancelled) break ;;
  esac
done

echo ""
echo "完成。查看："
echo "  详情  http://localhost:5173/wf/$ID   （前端详情路由以实际为准）"
echo "  trace curl -s -H \"X-Tenant-Id: $TENANT\" http://127.0.0.1:8000/api/v1/workflows/$ID/trace"
