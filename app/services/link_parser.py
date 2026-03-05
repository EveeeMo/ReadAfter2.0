"""链接内容解析"""
import json
import re
from urllib.parse import urlparse
import httpx
import trafilatura
from bs4 import BeautifulSoup


# 模拟浏览器请求，提高对微信公众号等反爬站点的兼容性
_DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


def extract_metadata(url: str, html: str | None = None) -> dict:
    """
    从 URL 或 HTML 提取元数据。
    返回: {title, summary, author, publish_date, full_text, platform}
    微信/小红书等反爬站点抓取失败时，仍返回可入库的最小信息，不抛 error。
    """
    platform = _guess_platform(url)

    if html is None:
        try:
            resp = httpx.get(url, follow_redirects=True, timeout=15, headers=_DEFAULT_HEADERS)
            resp.raise_for_status()
            html = resp.text
        except Exception as e:
            # 微信、小红书等抓取失败时，仍入库（至少保留链接）
            if _is_hard_to_fetch(url):
                return {
                    "title": _fallback_title(platform, url),
                    "summary": "链接来自反爬平台，需手动补充摘要",
                    "author": "",
                    "publish_date": "",
                    "full_text": "",
                    "platform": platform,
                }
            return {"error": str(e), "url": url}

    result = trafilatura.extract(
        html,
        url=url,
        include_comments=False,
        include_tables=False,
        output_format="txt",
    )
    full_text = result or ""

    # 用 BeautifulSoup 取 meta 和 title
    soup = BeautifulSoup(html, "html.parser")
    title = ""
    if soup.title:
        title = soup.title.get_text(strip=True)
    if not title:
        meta_og = soup.find("meta", property="og:title")
        if meta_og and meta_og.get("content"):
            title = meta_og["content"].strip()

    # 微信/小红书：正文被反爬时，优先用 og:description
    if not full_text or len(full_text.strip()) < 50:
        meta_desc = soup.find("meta", property="og:description")
        if meta_desc and meta_desc.get("content"):
            full_text = meta_desc["content"].strip()
        elif _is_hard_to_fetch(url) and len(full_text.strip()) < 50:
            full_text = "（内容需手动补充）"

    author = ""
    for meta in soup.find_all("meta", attrs={"name": re.compile(r"author|writer", re.I)}):
        if meta.get("content"):
            author = meta["content"].strip()
            break
    if not author:
        meta_og = soup.find("meta", property="article:author")
        if meta_og and meta_og.get("content"):
            author = meta_og["content"].strip()

    # 发布日期：多源提取（trafilatura 含 htmldate + JSON-LD，覆盖面广）
    publish_date = _extract_publish_date(html, url, soup)

    # 微信/小红书：过滤掉验证页等无意义标题
    if title and _is_useless_title(title):
        title = _fallback_title(platform, url)
    title = title or _fallback_title(platform, url)

    summary = full_text[:500].strip() + "..." if len(full_text) > 500 else full_text
    if not summary and full_text:
        summary = full_text[:300]

    return {
        "title": title,
        "summary": summary,
        "author": author,
        "publish_date": publish_date,
        "full_text": full_text,
        "platform": platform,
    }


def _is_date_from_cdn_path(html: str, date_str: str) -> bool:
    """
    判断日期是否可能来自 CDN/资源 URL 路径（如 res.wx.qq.com/.../2024-09-26/xxx.svg），
    此类日期并非文章发布日期，应丢弃。
    """
    norm = date_str.replace("/", "-")[:10]
    for m in re.finditer(re.escape(norm), html):
        start = max(0, m.start() - 80)
        end = min(len(html), m.end() + 80)
        ctx = html[start:end]
        # CDN 特征：/2024-09-26/ 或 /2024-09-26/xxx.svg 且附近有资源/域名
        if f"/{norm}/" in ctx or (f"-{norm}-" in ctx and ".svg" in ctx):
            if any(x in ctx for x in [".svg", ".png", ".jpg", ".js", "res.", "cdn.", "static.", "wx.qq"]):
                return True
    return False


def _extract_publish_date(html: str, url: str, soup: BeautifulSoup) -> str:
    """
    多源提取发布日期，优先级：trafilatura > meta 标签 > JSON-LD > time 元素 > URL 路径。
    返回 YYYY-MM-DD 或 ISO 格式。会过滤来自 CDN 资源路径的误检日期。
    """
    # 1. trafilatura（含 htmldate、JSON-LD、meta 等）
    try:
        doc = trafilatura.extract_metadata(html, default_url=url)
        if doc and getattr(doc, "date", None):
            s = str(doc.date).strip()
            if s and len(s) >= 8:
                norm = _normalize_date_string(s)
                if not _is_date_from_cdn_path(html, norm):
                    return norm
    except Exception:
        pass

    # 2. meta 标签
    for meta in soup.find_all("meta", attrs={"name": re.compile(r"date|publishdate", re.I)}):
        if meta.get("content"):
            s = meta["content"].strip()
            if s and len(s) >= 8:
                return _normalize_date_string(s)
    meta_og = soup.find("meta", property="article:published_time")
    if meta_og and meta_og.get("content"):
        s = meta_og["content"].strip()
        if s and len(s) >= 8:
            return _normalize_date_string(s)

    # 3. JSON-LD 中的 datePublished
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            raw = script.string and script.string.strip()
            if not raw:
                continue
            data = json.loads(raw)
            for item in [data] if isinstance(data, dict) else (data if isinstance(data, list) else []):
                if not isinstance(item, dict):
                    continue
                for key in ("datePublished", "date_published", "publishedDate", "publishDate"):
                    val = item.get(key)
                    if val and isinstance(val, str) and len(val) >= 8:
                        return _normalize_date_string(val)
                if "@graph" in item:
                    for g in item.get("@graph", []):
                        if isinstance(g, dict):
                            for key in ("datePublished", "date_published"):
                                val = g.get(key)
                                if val and isinstance(val, str) and len(val) >= 8:
                                    return _normalize_date_string(val)
        except (json.JSONDecodeError, TypeError):
            continue

    # 4. HTML5 <time datetime="...">
    for time_el in soup.find_all("time", datetime=True):
        dt = time_el.get("datetime", "").strip()
        if dt and len(dt) >= 8:
            return _normalize_date_string(dt)

    # 5. 微信公众号等：create_time 时间戳（常见于内嵌 script）
    for m in re.finditer(
        r"(?:create_time|createTime|ct)\s*[:=]\s*['\"]?(\d{10})['\"]?",
        html,
        re.I,
    ):
        try:
            ts = int(m.group(1))
            if 1_600_000_000 < ts < 2_000_000_000:  # 2020–2033
                from datetime import datetime
                dt = datetime.fromtimestamp(ts)
                return dt.strftime("%Y-%m-%d %H:%M:%S")[:19]
        except (ValueError, OSError):
            continue

    # 6. URL 路径中的日期（如 /2024/01/15/article、/blog/2024-09-26/）
    m = re.search(r"/(\d{4})[/\-](\d{1,2})[/\-](\d{1,2})(?:/|$)", url)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"

    return ""


def _normalize_date_string(s: str) -> str:
    """将常见日期格式归一为 YYYY-MM-DD 或 YYYY-MM-DDTHH:MM:SS，供飞书 Date 字段解析"""
    s = (s or "").strip()
    if not s or len(s) < 8:
        return ""
    # 中文格式：2024年1月15日、2024-01-15 发布
    m = re.search(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*", s)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    s = s[:19].replace("/", "-")
    return s


def _is_hard_to_fetch(url: str) -> bool:
    """是否为反爬较强的平台"""
    d = urlparse(url).netloc.lower()
    return "mp.weixin" in d or "weixin" in d or "xiaohongshu" in d or "xhslink" in d


def _is_useless_title(title: str) -> bool:
    """是否为验证页等无意义标题"""
    bad = ("环境异常", "验证", "安全验证", "访问提示", "去验证", "完成验证")
    return any(b in title for b in bad) or len(title.strip()) < 4


def _fallback_title(platform: str, url: str) -> str:
    """抓取失败时的占位标题"""
    if "微信" in platform:
        return "微信公众号文章"
    if "小红书" in platform:
        return "小红书内容"
    return f"{platform} 链接"


def _guess_platform(url: str) -> str:
    """根据 URL 推断平台"""
    d = urlparse(url).netloc.lower()
    if "mp.weixin" in d or "weixin" in d:
        return "微信公众号"
    if "xiaohongshu" in d or "xhslink" in d:
        return "小红书"
    if "zhihu.com" in d:
        return "知乎"
    if "juejin" in d:
        return "掘金"
    if "bilibili" in d:
        return "哔哩哔哩"
    if "douban" in d:
        return "豆瓣"
    if "notion" in d:
        return "Notion"
    if "medium.com" in d:
        return "Medium"
    return d.split(".")[-2] if "." in d else d or "未知"
