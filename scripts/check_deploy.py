#!/usr/bin/env python3
"""
检查 AI Builder Space 部署状态和日志
需要环境变量 AI_BUILDER_TOKEN
用法: python scripts/check_deploy.py
       python scripts/check_deploy.py logs     # 查看日志
       python scripts/check_deploy.py logs build  # 构建日志
       python scripts/check_deploy.py logs runtime # 运行日志
"""
import os
import sys
import json
import httpx

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

BASE = "https://space.ai-builders.com/backend"
SERVICE = "readafter2"


def main():
    token = os.getenv("AI_BUILDER_TOKEN")
    if not token:
        print("❌ 请设置环境变量 AI_BUILDER_TOKEN")
        print("   示例: export AI_BUILDER_TOKEN=你的token")
        sys.exit(1)

    headers = {"Authorization": f"Bearer {token}"}

    cmd = sys.argv[1] if len(sys.argv) > 1 else "detail"

    if cmd == "list":
        # 列出所有部署
        resp = httpx.get(f"{BASE}/v1/deployments", headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        deployments = data.get("deployments", [])
        print("\n📋 我的部署列表:\n")
        for d in deployments:
            name = d.get("service_name", "?")
            status = d.get("status", "?")
            url = d.get("url", "")
            print(f"  服务名: {name}")
            print(f"  状态:   {status}")
            print(f"  地址:   {url or '(暂无)'}")
            print()
        if not deployments:
            print("  (暂无部署)")
        return

    elif cmd == "logs":
        log_type = sys.argv[2] if len(sys.argv) > 2 else "runtime"
        if log_type not in ("build", "runtime"):
            log_type = "runtime"
        print(f"\n📜 获取 {log_type} 日志 (最多等待 15 秒)...\n")
        url = f"{BASE}/v1/deployments/{SERVICE}/logs"
        resp = httpx.get(url, params={"log_type": log_type, "timeout": 15}, headers=headers, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        logs = data.get("logs", "")
        print(logs if logs else "(暂无日志)")
        return

    # status / detail: 查询单个服务状态
    print(f"\n🔍 查询服务 {SERVICE} 状态...\n")
    resp = httpx.get(f"{BASE}/v1/deployments/{SERVICE}", headers=headers, timeout=30)
    if resp.status_code == 404:
        print("❌ 未找到该服务，可能尚未部署或服务名不对")
        sys.exit(1)
    resp.raise_for_status()
    d = resp.json()
    print(json.dumps(d, indent=2, ensure_ascii=False))
    print("\n--- 状态说明 ---")
    print("  HEALTHY: 正常运行")
    print("  deploying/queued: 部署中")
    print("  UNHEALTHY/ERROR: 部署失败或运行异常")
    print("  查看日志: python scripts/check_deploy.py logs [build|runtime]")


if __name__ == "__main__":
    main()
