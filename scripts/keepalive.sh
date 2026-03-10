#!/bin/bash
# 定期 ping 线上服务，防止 Koyeb 因无流量进入 deep sleep
# 用法：crontab -e 添加：*/4 * * * * /path/to/scripts/keepalive.sh
# 或手动运行：./scripts/keepalive.sh

URL="${KEEPALIVE_URL:-https://readafter2.ai-builders.space/health}"
curl -s -o /dev/null -w "Keepalive %{http_code} @ $(date)\n" --max-time 30 "$URL"
