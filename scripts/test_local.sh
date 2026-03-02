#!/bin/bash
# 本地测试脚本

cd "$(dirname "$0")/.."
echo "=== ReadAfter2.0 本地测试 ==="

# 检查 .env
if [ ! -f .env ]; then
  echo "请先创建 .env 并填入配置："
  echo "  cp .env.example .env"
  echo "  编辑 .env，填入 FEISHU_APP_SECRET 和 AI_BUILDER_TOKEN"
  exit 1
fi

# 启动服务（后台）
echo "启动服务..."
uvicorn app.main:app --host 0.0.0.0 --port 8000 &
PID=$!
sleep 3

# 测试
echo ""
echo "1. 健康检查..."
curl -s http://127.0.0.1:8000/health | head -1

echo ""
echo "2. 飞书连接..."
curl -s "http://127.0.0.1:8000/test/feishu"

echo ""
echo ""
echo "3. 链接解析（可替换为你自己的链接）..."
curl -s "http://127.0.0.1:8000/test/link?url=https://www.python.org/"

echo ""
echo ""
echo "4. RAG 问答..."
curl -s "http://127.0.0.1:8000/test/rag?q=有什么内容"

echo ""
echo ""
echo "=== 测试完成。服务仍在运行，按 Ctrl+C 停止 ==="
wait $PID 2>/dev/null || true
