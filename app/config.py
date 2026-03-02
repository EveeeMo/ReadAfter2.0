"""ReadAfter2.0 配置"""
import os
from pathlib import Path

# 从项目根目录加载 .env
_env_path = Path(__file__).resolve().parent.parent / ".env"
if _env_path.exists():
    from dotenv import load_dotenv
    load_dotenv(_env_path)

# 服务端口（AI Builder Space 通过 PORT 环境变量注入）
PORT = int(os.getenv("PORT", "8000"))

# 飞书配置
FEISHU_APP_ID = os.getenv("FEISHU_APP_ID", "")
FEISHU_APP_SECRET = os.getenv("FEISHU_APP_SECRET", "")
FEISHU_BITABLE_APP_TOKEN = os.getenv("FEISHU_BITABLE_APP_TOKEN", "")
FEISHU_BITABLE_TABLE_ID = os.getenv("FEISHU_BITABLE_TABLE_ID", "")

# AI Builder Space
AI_BUILDER_TOKEN = os.getenv("AI_BUILDER_TOKEN", "")
AI_BUILDER_BASE_URL = "https://space.ai-builders.com/backend/v1"
