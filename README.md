# ReadAfter2.0

飞书机器人：收集链接/图片中的内容到多维表格，并支持基于已收集内容的 RAG 问答。

## 功能

- **链接**：发送链接 → 解析简介、作者、日期、正文 → 写入飞书多维表格
- **图片**：发送图片 → 多模态识别截图中的视频/文章链接 → 解析并入库
- **提问**：发送问题 → RAG 检索已收集内容 → 生成回答

## 部署到 AI Builder Space

### 1. 推送到 GitHub

```bash
cd /Users/eve/Desktop/ReadAfter

# 方式一：使用脚本（推荐）
chmod +x scripts/setup_github.sh
./scripts/setup_github.sh

# 方式二：手动
git init
git add .
git reset HEAD .env  # 确保不提交
git commit -m "ReadAfter2.0 initial"
git remote add origin https://github.com/YOUR_USERNAME/ReadAfter2.0.git
git branch -M main
git push -u origin main
```

**重要**：仓库需为**公开**；不要将 `.env` 提交到 Git。先在 https://github.com/new 创建空仓库 `ReadAfter2.0`。

### 2. 配置环境变量

部署时在 `env_vars` 中传入：

| 变量名 | 说明 |
|-------|------|
| `FEISHU_APP_ID` | 飞书应用 App ID |
| `FEISHU_APP_SECRET` | 飞书应用 App Secret |
| `FEISHU_BITABLE_APP_TOKEN` | 多维表格 app_token |
| `FEISHU_BITABLE_TABLE_ID` | 多维表格 table_id |

`AI_BUILDER_TOKEN` 由平台自动注入，无需配置。

### 3. 飞书多维表格字段

确保表格包含以下字段（名称需一致）：

| 字段名 | 类型 |
|-------|------|
| 内容 | 文本/链接 |
| 摘要 | 文本 |
| 作者 | 文本 |
| 平台 | 文本 |
| 状态 | 文本（默认「仅记录」） |
| 发布日期 | 日期/文本 |
| 收集日期 | 日期/文本 |
| 全文 | 文本（长文本，用于 RAG） |
| 来源类型 | 文本（链接/图片识别） |

### 4. 飞书机器人配置

1. 在飞书开放平台创建应用，获取 App ID 和 App Secret
2. 开启「机器人」能力，配置事件订阅
3. 请求地址：`https://readafter2.ai-builders.space/webhook/feishu`（以实际部署域名为准）
4. 订阅 `im.message.receive_v1`（接收消息）
5. 将机器人拉入群聊或发起私聊测试

### 5. 触发部署

1. 复制 `deploy-config.example.json` 为 `deploy-config.json`
2. 填入你的 GitHub 仓库 URL、飞书相关变量（App Secret 勿泄露）
3. 执行：

```bash
export AI_BUILDER_TOKEN=你的token
chmod +x scripts/deploy.sh
./scripts/deploy.sh
```

或手动调用 API：

```bash
curl -X POST "https://space.ai-builders.com/backend/v1/deployments" \
  -H "Authorization: Bearer $AI_BUILDER_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"repo_url":"https://github.com/YOUR_USER/ReadAfter2.0","service_name":"readafter2","branch":"main","env_vars":{"FEISHU_APP_ID":"...","FEISHU_APP_SECRET":"...","FEISHU_BITABLE_APP_TOKEN":"...","FEISHU_BITABLE_TABLE_ID":"..."}}'
```

部署需 5–10 分钟，成功后访问：`https://readafter2.ai-builders.space`

## 本地开发与测试

### 1. 安装依赖

```bash
cd /Users/eve/Desktop/ReadAfter
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env，填入 FEISHU_APP_SECRET 和 AI_BUILDER_TOKEN
```

### 3. 启动服务

```bash
uvicorn app.main:app --reload --port 8000
```

### 4. 测试接口（部署前验证）

| 测试项 | 请求 | 说明 |
|-------|------|------|
| 健康检查 | `GET http://127.0.0.1:8000/health` | 服务是否正常 |
| 飞书连接 | `GET http://127.0.0.1:8000/test/feishu` | Token 和表格读取 |
| 链接解析入库 | `GET http://127.0.0.1:8000/test/link?url=https://www.python.org` | 解析链接并写入表格 |
| RAG 问答 | `GET http://127.0.0.1:8000/test/rag?q=有什么内容` | 基于已收集内容回答 |

在浏览器打开上述 URL，或用 `curl` 测试，例如：

```bash
curl "http://127.0.0.1:8000/test/feishu"
curl "http://127.0.0.1:8000/test/link?url=https://example.com"
```

如需测试飞书回调，可使用 ngrok 将本地 8000 端口暴露为公网 URL，在飞书后台配置该 URL。
