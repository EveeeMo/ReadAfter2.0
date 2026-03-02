#!/bin/bash
# 部署 ReadAfter2.0 到 AI Builder Space

set -e
cd "$(dirname "$0")/.."

CONFIG="deploy-config.json"
if [ ! -f "$CONFIG" ]; then
  echo "请先复制 deploy-config.example.json 为 deploy-config.json，并填入："
  echo "  - repo_url: 你的 GitHub 仓库地址（需已推送）"
  echo "  - service_name: readafter2"
  echo "  - branch: main"
  echo "  - env_vars: 飞书 App ID、Secret、多维表格 token 等"
  exit 1
fi

if [ -z "$AI_BUILDER_TOKEN" ]; then
  echo "请设置环境变量 AI_BUILDER_TOKEN"
  echo "  export AI_BUILDER_TOKEN=你的token"
  exit 1
fi

echo "=== 触发 AI Builder Space 部署 ==="
curl -s -X POST "https://space.ai-builders.com/backend/v1/deployments" \
  -H "Authorization: Bearer $AI_BUILDER_TOKEN" \
  -H "Content-Type: application/json" \
  -d @"$CONFIG"

echo ""
echo "部署已提交，通常需 5–10 分钟完成。"
echo "查看状态: curl -s -H \"Authorization: Bearer \$AI_BUILDER_TOKEN\" https://space.ai-builders.com/backend/v1/deployments/readafter2"
