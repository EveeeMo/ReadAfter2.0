# 本地调试飞书机器人

在本地运行 FastAPI，通过 ngrok 暴露公网地址，让飞书 webhook 能访问你的本机。

---

## 1. 环境准备

确保 `.env` 中有飞书配置（可从 `deploy-config.json` 复制）：

```
FEISHU_APP_ID=cli_xxx
FEISHU_APP_SECRET=xxx
FEISHU_BITABLE_APP_TOKEN=xxx
FEISHU_BITABLE_TABLE_ID=xxx
```

---

## 2. 启动本地服务

```bash
cd /Users/eve/Desktop/ReadAfter
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

`--reload` 会在修改代码时自动重启。

---

## 3. 用 ngrok 暴露到公网

飞书只会请求公网 URL，需要把本机 8000 端口暴露出去。

### 安装 ngrok

```bash
brew install ngrok
# 或从 https://ngrok.com 下载
```

### 启动隧道

```bash
ngrok http 8000
```

终端会显示类似：

```
Forwarding  https://xxxx-xx-xx-xx-xx.ngrok-free.app -> http://localhost:8000
```

记下 `https://xxxx...ngrok-free.app` 这个地址。

---

## 4. 修改飞书事件订阅 URL

1. 打开 [飞书开放平台](https://open.feishu.cn/app/cli_a7539b9a077ed01c)
2. **功能** → **机器人** → **事件订阅**
3. 将「请求地址」改为：
   ```
   https://你的ngrok地址.ngrok-free.app/webhook/feishu
   ```
4. 点击**保存**（飞书会发校验请求，服务需在运行）

---

## 5. 测试

- 向机器人发 **「ping」**，应收到 `ReadAfter2.0 已收到～ ✅`
- 访问 `https://你的ngrok地址.ngrok-free.app/webhook/feishu/debug` 查看最近一次 webhook

---

## 6. 调试完成后

- 停止 ngrok（Ctrl+C）
- 停止 uvicorn（Ctrl+C）
- 若需恢复线上部署，将飞书事件订阅 URL 改回 `https://readafter2.ai-builders.space/webhook/feishu`
