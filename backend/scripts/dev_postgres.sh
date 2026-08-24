#!/usr/bin/env bash
# 用本机已安装的 PostgreSQL 二进制（无需 Docker / 管理员权限）初始化并启动
# 项目专属实例：独立数据目录 .localdata/pgdata，端口 15433。
# 幂等：重复执行只会启动已存在的集群。连接串见输出。
set -euo pipefail

PGBIN="${PGBIN:-/f/postgresql/bin}"          # 本机 PG17 安装位置
PORT="${PORT:-15433}"
DB_NAME="crosseshop"
DB_USER="cesa"
DB_PASS="cesa_secret"

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
DATA="$ROOT/.localdata/pgdata"
mkdir -p "$ROOT/.localdata"

if [ ! -f "$DATA/PG_VERSION" ]; then
  printf '%s' "$DB_PASS" > "$ROOT/.localdata/.pwfile"
  "$PGBIN/initdb.exe" -D "$DATA" -U "$DB_USER" \
    --auth=scram-sha-256 --pwfile="$ROOT/.localdata/.pwfile" -E UTF8 --locale=C
  rm -f "$ROOT/.localdata/.pwfile"
fi

"$PGBIN/pg_ctl.exe" -D "$DATA" -o "-p $PORT" -l "$DATA/server.log" start || true

export PGPASSWORD="$DB_PASS"
for _ in $(seq 1 10); do
  "$PGBIN/pg_isready.exe" -h 127.0.0.1 -p "$PORT" -U "$DB_USER" -q && break
  sleep 1
done
"$PGBIN/createdb.exe" -h 127.0.0.1 -p "$PORT" -U "$DB_USER" "$DB_NAME" 2>/dev/null \
  || echo "db $DB_NAME already exists"

echo
echo "DATABASE_URL=postgresql+asyncpg://${DB_USER}:${DB_PASS}@127.0.0.1:${PORT}/${DB_NAME}"
