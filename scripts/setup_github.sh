#!/bin/bash
# 将 ReadAfter2.0 推送到 GitHub

set -e
cd "$(dirname "$0")/.."

echo "=== ReadAfter2.0 推送到 GitHub ==="

# 检查是否已初始化
if [ ! -d .git ]; then
  git init
  echo "已初始化 Git 仓库"
fi

# 确认不提交敏感文件
if git check-ignore -q .env 2>/dev/null; then
  echo "✓ .env 已在 .gitignore 中"
else
  echo "警告: 请确保 .env 在 .gitignore 中"
fi

# 添加项目文件（排除 .env、.venv 等）
git add app/ scripts/ Dockerfile requirements.txt .gitignore README.md .env.example deploy-config.example.json
git status

# 确保不提交敏感文件
git reset HEAD .env deploy-config.json 2>/dev/null || true

echo ""
read -p "请输入你的 GitHub 用户名: " GITHUB_USER
if [ -z "$GITHUB_USER" ]; then
  echo "已取消"
  exit 1
fi

REPO_URL="https://github.com/${GITHUB_USER}/ReadAfter2.0"

# 检查 remote
if git remote get-url origin 2>/dev/null; then
  echo "当前 origin: $(git remote get-url origin)"
  read -p "是否更新为 $REPO_URL? (y/n) " upd
  if [ "$upd" = "y" ]; then
    git remote set-url origin "$REPO_URL"
  fi
else
  git remote add origin "$REPO_URL"
fi

echo ""
echo "请在 GitHub 上创建仓库: https://github.com/new?name=ReadAfter2.0"
echo "仓库名: ReadAfter2.0, 可见性: Public"
echo ""
read -p "仓库已创建？按 Enter 继续推送..."

git add -A
git reset HEAD .env 2>/dev/null || true
git commit -m "ReadAfter2.0: 飞书链接/图片收集 + RAG 问答" || true
git branch -M main
git push -u origin main

echo ""
echo "✓ 已推送到 $REPO_URL"
echo "仓库 URL: $REPO_URL"
