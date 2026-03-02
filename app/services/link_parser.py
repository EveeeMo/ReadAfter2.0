"""链接内容解析"""
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

    publish_date = ""
    for meta in soup.find_all("meta", attrs={"name": re.compile(r"date|publishdate", re.I)}):
        if meta.get("content"):
            publish_date = meta["content"].strip()
            break
    if not publish_date:
        meta_og = soup.find("meta", property="article:published_time")
        if meta_og and meta_og.get("content"):
            publish_date = meta_og["content"].strip()

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
