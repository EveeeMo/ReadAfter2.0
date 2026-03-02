"""飞书机器人消息解析与回复"""
import base64
import json
import re
import httpx
from app.feishu.auth import get_tenant_access_token


def parse_event(body: dict) -> dict | None:
    """
    解析飞书事件回调。
    支持 Schema v1（type=event_callback）和 v2（schema=2.0）。
    返回: {"type": "url"|"image"|"text", "content": ..., "chat_id": ..., "msg_id": ..., "user_id": ...}
    或 None 表示无需处理（如校验请求）。
    """
    # 飞书 URL 校验
    if body.get("type") == "url_verification":
        return {"type": "url_verification", "challenge": body.get("challenge", "")}

    # 支持 v1 (type=event_callback) 和 v2 (schema=2.0)
    if body.get("type") == "event_callback":
        event = body.get("event", {})
    elif body.get("schema") == "2.0" and body.get("event"):
        event = body.get("event", {})
    else:
        return None

    msg_obj = event.get("message", {})
    msg_type = msg_obj.get("message_type")
    chat_id = msg_obj.get("chat_id", "")
    msg_id = msg_obj.get("message_id", "") or msg_obj.get("message_id_v2", "")
    chat_type = msg_obj.get("chat_type", "")
    sender_id = event.get("sender", {}).get("sender_id", {})
    # sender_id 可能是对象 {open_id, user_id} 或直接是 id 字符串
    if isinstance(sender_id, dict):
        user_id = sender_id.get("user_id", "")
        open_id = sender_id.get("open_id", "")
    else:
        user_id = str(sender_id or "")
        open_id = user_id if (user_id.startswith("ou_") or user_id.startswith("oc_")) else ""

    if not msg_type:
        return None

    content_str = event.get("message", {}).get("content", "{}")
    try:
        content = json.loads(content_str)
    except json.JSONDecodeError:
        return None

    # 文本消息
    if msg_type == "text":
        text = content.get("text", "").strip()
        if not text:
            return None
        urls_found = re.findall(r"https?://[^\s\)\]\"\']+", text)
        urls_found = [u.rstrip(".,;:!?）】") for u in urls_found]
        urls_found = list(dict.fromkeys(urls_found))
        if urls_found:
            first_start = text.find(urls_found[0])
            extra = re.sub(r"\s+", " ", text[:first_start].strip())[:500] if first_start > 0 else ""
            if len(urls_found) == 1:
                return {"type": "url", "content": urls_found[0], "extra": extra, "chat_id": chat_id, "msg_id": msg_id, "user_id": user_id, "chat_type": chat_type, "open_id": open_id}
            return {"type": "urls", "content": urls_found, "extra": extra, "chat_id": chat_id, "msg_id": msg_id, "user_id": user_id, "chat_type": chat_type, "open_id": open_id}
        return {"type": "text", "content": text, "chat_id": chat_id, "msg_id": msg_id, "user_id": user_id, "chat_type": chat_type, "open_id": open_id}

    # 图片消息
    if msg_type == "image":
        # 飞书图片在 content 的 image_key 中
        image_key = content.get("image_key", "")
        if image_key:
            return {"type": "image", "content": image_key, "chat_id": chat_id, "msg_id": msg_id, "user_id": user_id, "chat_type": chat_type, "open_id": open_id}
        return None

    return None


def get_image_for_vision(image_key: str) -> str:
    """
    通过 image_key 获取图片，返回可用于多模态 API 的 data URL。
    飞书图片接口返回二进制，转为 base64 供 vision 模型使用。
    """
    token = get_tenant_access_token()
    resp = httpx.get(
        f"https://open.feishu.cn/open-apis/im/v1/images/{image_key}",
        params={"image_type": "message"},
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    resp.raise_for_status()
    content_type = resp.headers.get("content-type", "image/png")
    if "application/json" in content_type:
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"获取图片失败: {data}")
        url = data.get("data", {}).get("url", "")
        if url:
            return url
    b64 = base64.b64encode(resp.content).decode()
    return f"data:{content_type.split(';')[0]};base64,{b64}"


def reply_message(chat_id: str, msg_id: str, text: str, chat_type: str = "", open_id: str = "") -> None:
    """回复消息到群聊/私聊。私聊(p2p)需用 open_id，群聊用 chat_id"""
    token = get_tenant_access_token()
    # 私聊必须用 open_id，否则发不出去
    if chat_type == "p2p" and open_id:
        rid_type, rid = "open_id", open_id
    else:
        rid_type, rid = "chat_id", chat_id
    if not rid:
        raise ValueError("缺少 receive_id（chat_id 或 open_id）")
    resp = httpx.post(
        "https://open.feishu.cn/open-apis/im/v1/messages",
        params={"receive_id_type": rid_type, "receive_id": rid},
        json={
            "msg_type": "text",
            "content": json.dumps({"text": text}),
        },
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"回复消息失败: {data}")
