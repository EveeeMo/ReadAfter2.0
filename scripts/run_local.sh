#!/bin/bash
# 本地调试：启动 FastAPI
cd "$(dirname "$0")/.."
# 自动激活 .venv（如果存在）
if [ -d ".venv" ]; then
  source .venv/bin/activate
fi
echo "启动 ReadAfter2.0 本地服务 (http://localhost:8000)"
echo "用 ngrok http 8000 暴露后，将飞书事件订阅改为 ngrok 的 https 地址"
echo ""
python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
