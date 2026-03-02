"""飞书多维表格 API"""
import time
import httpx
from datetime import datetime
from app.config import FEISHU_BITABLE_APP_TOKEN, FEISHU_BITABLE_TABLE_ID


def _to_timestamp_ms(value: str | int) -> int | None:
    """将日期字符串转为 Unix 毫秒时间戳，飞书 Date 字段需要"""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)) and value > 0:
        return int(value) if value > 1e12 else int(value * 1000)
    s = str(value).strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M"):
        try:
            dt = datetime.strptime(s[:19], fmt)
            return int(dt.timestamp() * 1000)
        except ValueError:
            continue
    return None
from app.feishu.auth import get_tenant_access_token

# 字段名映射（根据你的表格结构调整）
FIELD_MAP = {
    "内容": "content",
    "摘要": "summary",
    "作者": "author",
    "平台": "platform",
    "状态": "status",
    "发布日期": "publish_date",
    "收集日期": "collect_date",
    "全文": "full_text",  # 用于 RAG
    "来源类型": "source_type",  # 链接 / 图片识别
}


def _get_headers() -> dict:
    token = get_tenant_access_token()
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def add_record(
    content: str,
    summary: str = "",
    author: str = "",
    platform: str = "",
    status: str = "仅记录",
    publish_date: str = "",
    full_text: str = "",
    source_type: str = "链接",
    content_url: str = "",
) -> dict:
    """
    向多维表格添加记录。字段名需与飞书表格中的实际字段一致。
    日期字段需为 Unix 毫秒时间戳。内容列为 Link 类型时需传 content_url。
    """
    collect_ts = int(time.time() * 1000)
    publish_ts = _to_timestamp_ms(publish_date) if publish_date else None

    # 内容列为 Link 类型时格式为 {link, text}
    if content_url and (content_url.startswith("http://") or content_url.startswith("https://")):
        content_value = {"link": content_url, "text": (content or content_url)[:2000]}
    else:
        content_value = content

    fields = {
        "内容": content_value,
        "摘要": summary or "",
        "作者": author or "",
        "平台": platform or "",
        "状态": status or "仅记录",
        "收集日期": collect_ts,
    }
    if publish_ts is not None:
        fields["发布日期"] = publish_ts
    txt = (full_text or summary or "")[:50000]
    if txt:
        fields["全文"] = txt
    if source_type:
        fields["来源类型"] = source_type

    url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{FEISHU_BITABLE_APP_TOKEN}/tables/{FEISHU_BITABLE_TABLE_ID}/records"
    resp = httpx.post(url, json={"fields": fields}, headers=_get_headers(), timeout=15)
    data = resp.json()

    # 若表格无「全文」「来源类型」列，则去掉后重试
    if data.get("code") != 0 and "field" in str(data.get("msg", "")).lower():
        for key in ["全文", "来源类型"]:
            fields.pop(key, None)
        resp2 = httpx.post(url, json={"fields": fields}, headers=_get_headers(), timeout=15)
        data = resp2.json()

    if data.get("code") != 0:
        raise RuntimeError(f"飞书表格写入失败: {data}")
    return data.get("data", {})


def list_records(limit: int = 50) -> list:
    """获取记录列表，用于 RAG 检索"""
    url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{FEISHU_BITABLE_APP_TOKEN}/tables/{FEISHU_BITABLE_TABLE_ID}/records"
    params = {"page_size": min(limit, 100)}
    all_records = []
    page_token = None

    while True:
        if page_token:
            params["page_token"] = page_token
        resp = httpx.get(url, params=params, headers=_get_headers(), timeout=15)
        if resp.status_code != 200:
            try:
                err_body = resp.json()
                raise RuntimeError(f"HTTP {resp.status_code}: {err_body}")
            except Exception:
                raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:500]}")
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"飞书表格读取失败: {data}")

        items = data.get("data", {}).get("items", [])
        all_records.extend(items)
        page_token = data.get("data", {}).get("page_token")
        if not page_token or len(all_records) >= limit:
            break

    return all_records[:limit]
