@echo off
rem 演示栈开机自启（Windows 登录任务 CrossEshopDemoStack 调用）：
rem 商城 :8001 + 同源应用 :8010（API + 前端 dist），不重置数据库、保留历史演示数据。
rem 隧道由 cloudflared Windows 服务独立承载，与此脚本无关。
rem 端口被占用时新实例自动退出，不影响已在运行的旧实例。

set ROOT=%USERPROFILE%\WorkBuddy\cross-eshop-agent

rem ---- 先起商城 :8001（发布回写链接用正式域名）----
set PUBLIC_BASE_URL=https://shop.tofu256.ccwu.cc
start "shopverse-8001" /min "%ROOT%\backend\.venv\Scripts\python.exe" "%ROOT%\mock-marketplace\server.py" --demo

rem ---- 起同源应用 :8010（cwd 必须是 backend：.env 与相对路径都在此解析）----
cd /d "%ROOT%\backend"
set FRONTEND_DIST_PATH=%ROOT%\frontend\dist
set DATABASE_URL=sqlite+aiosqlite:///./.localdata/cloud_demo.db
set CHECKPOINT_DB_PATH=.localdata/checkpoints_cloud.db
set AUTO_APPROVE=true
set MOCK_MARKETPLACE_URL=http://127.0.0.1:8001
"%ROOT%\backend\.venv\Scripts\python.exe" -m uvicorn app.api.main:app --host 0.0.0.0 --port 8010
