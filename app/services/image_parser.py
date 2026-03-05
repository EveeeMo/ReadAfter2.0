"""图片中视频链接识别（多模态）"""
import re
from urllib.parse import quote
from openai import OpenAI
from app.config import AI_BUILDER_TOKEN, AI_BUILDER_BASE_URL


def extract_urls_from_image(image_url_or_data: str) -> list[str]:
    """
    通过多模态模型识别图片中的视频/内容链接。
    若无链接，则提取视频信息并构造搜索 URL 作为备选。
    """
    result = _analyze_image(image_url_or_data)
    return result["urls"]


def analyze_image(image_url_or_data: str, trace: list | None = None) -> dict:
    """
    分析图片：提取链接 + 若无链接则提取视频/内容信息并构造可用的搜索 URL。
    返回: {urls: [...], fallback: {title, channel, platform, search_url} | None}
    若 trace 非 None，则向 trace 追加每步的输入输出。
    """
    result = _analyze_image(image_url_or_data, trace)
    return result


def _analyze_image(image_url_or_data: str, trace: list | None = None) -> dict:
    if not AI_BUILDER_TOKEN:
        return {"urls": [], "fallback": None}

    def add_step(name: str, payload: dict):
        if trace is not None:
            trace.append({"step": name, **payload})

    client = OpenAI(api_key=AI_BUILDER_TOKEN, base_url=AI_BUILDER_BASE_URL)
    img_block = {"type": "image_url", "image_url": {"url": image_url_or_data}}

    # 第一步：找链接
    resp1 = client.chat.completions.create(
        model="kimi-k2.5",
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "请仔细观察这张图片，找出其中包含的所有链接（视频链接、文章链接等）。"
                        "只输出链接 URL，每行一个。如果没有找到任何链接，请回复：无",
                    },
                    img_block,
                ],
            }
        ],
        max_tokens=500,
    )
    text1 = (resp1.choices[0].message.content or "").strip()
    urls = []
    if text1 and "无" not in text1:
        for line in text1.splitlines():
            found = re.findall(r"https?://[^\s\)\]\"\']+", line.strip())
            urls.extend(found)
    urls = list(dict.fromkeys(urls))
    add_step("1_找链接", {
        "prompt": "找出图片中所有链接，每行一个；无则回复：无",
        "model": "kimi-k2.5",
        "model_response": text1[:500],
        "urls_extracted": urls,
    })

    # 第二步：若无链接，提取视频/内容信息（用于 find_best_video_url 等）
    # 发布日期统一从最终链接抓取，不在此提取
    fallback = None
    if not urls:
        try:
            text2 = ""
            for model in ("kimi-k2.5", "gemini-2.5-pro"):
                try:
                    resp2 = client.chat.completions.create(
                        model=model,
                        messages=[
                            {
                                "role": "user",
                                "content": [
                                    {
                                        "type": "text",
                                        "text": "这是视频/内容截图。请只输出三行，不要任何分析、介绍或解释：\n"
                                        "平台: （从界面判断，如YouTube/B站/小红书）\n"
                                        "标题: （图中视频或内容的真实标题，直接照抄）\n"
                                        "创作者: （图中作者/频道名，无则留空）\n"
                                        "仅输出这三行。",
                                    },
                                    img_block,
                                ],
                            }
                        ],
                        max_tokens=400,
                    )
                    text2 = (resp2.choices[0].message.content or "").strip()
                    break
                except Exception as e:
                    if "400" in str(e) or "thinking" in str(e).lower():
                        continue
                    raise
            platform, title, creator = _parse_video_info(text2)
            if not platform or platform == "未知":
                platform = _guess_platform_from_title(title)
            search_url = _make_search_url(platform, title, creator)
            fallback = {
                "title": title or "（图片中的视频/内容）",
                "channel": creator,
                "platform": platform or "未知",
                "search_url": search_url,
            }
            add_step("2_提取视频信息", {
                "model_response": text2[:500],
                "parsed": {"platform": platform, "title": title, "creator": creator},
                "fallback": fallback,
            })
        except Exception as e:
            add_step("2_提取视频信息", {"error": str(e)})
            platform = "B站"
            search_url = "https://search.bilibili.com/all?keyword=video"
            fallback = {
                "title": "视频截图内容",
                "channel": "",
                "platform": platform,
                "search_url": search_url,
            }

    return {"urls": urls, "fallback": fallback}


def _parse_video_info(text: str) -> tuple[str, str, str]:
    """解析模型返回的平台、标题、创作者，支持中英文"""
    platform = title = creator = ""
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = re.split(r"[:：]\s*", line, 1)
        val = parts[-1].strip() if len(parts) > 1 else ""
        lower = line.lower()
        if lower.startswith("平台") or lower.startswith("platform"):
            platform = val
        elif lower.startswith("标题") or lower.startswith("title"):
            title = val
        elif lower.startswith("创作者") or lower.startswith("creator") or lower.startswith("频道") or lower.startswith("channel"):
            creator = val
    if not title and text:
        first_line = next((l.strip() for l in text.splitlines() if len(l.strip()) > 8), "")
        if first_line and "平台" not in first_line[:4] and "platform" not in first_line[:8].lower():
            title = first_line[:200]
    # 过滤模型套话：若标题像分析性语句，尝试从文中找更像真实标题的行
    bad_starts = ("我", "请", "从", "这", "我们", "您", "分析", "帮", "这张", "界面")
    if title and any(title.startswith(s) for s in bad_starts) and len(title) > 25:
        best = ""
        for line in text.splitlines():
            line = line.strip()
            if len(line) < 10 or line.startswith("平台") or line.startswith("Platform"):
                continue
            if ("万" in line or "|" in line or "收入" in line or "干货" in line) and len(line) > len(best):
                best = line[:200]
        if best:
            title = best
    return platform, title, creator


def find_best_video_url(platform: str, title: str, creator: str, trace: list | None = None) -> str | None:
    """
    用联网模型根据平台、标题、创作者选取最合适的视频直链。
    仅支持 YouTube 和 B 站。返回 None 表示未找到。
    """
    if not AI_BUILDER_TOKEN:
        return None

    pl = (platform or "").lower()
    if "youtube" not in pl and "油管" not in pl and "b站" not in pl and "bilibili" not in pl:
        return None

    client = OpenAI(api_key=AI_BUILDER_TOKEN, base_url=AI_BUILDER_BASE_URL)
    prompt = (
        f"请搜索并找出最匹配的{platform or '视频'}链接。\n"
        f"标题: {title or '(未知)'}\n"
        f"创作者/频道: {creator or '(未知)'}\n\n"
        "要求：只输出一个直接视频链接（如 youtube.com/watch?v=xxx 或 bilibili.com/video/xxx），不要任何解释。"
        "如果找不到，请回复: NOT_FOUND"
    )
    try:
        resp = client.chat.completions.create(
            model="supermind-agent-v1",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=500,
        )
        text = (resp.choices[0].message.content or "").strip()
        if "NOT_FOUND" in text.upper():
            if trace is not None:
                trace.append({"step": "3_选取最合适链接", "prompt": prompt[:400], "model_response": text[:500], "url_picked": None})
            return None
        found = re.findall(r"https?://[^\s\)\]\"\']+", text)
        best = None
        for u in found:
            if "youtube.com/watch" in u or "youtu.be/" in u or "bilibili.com/video" in u:
                best = u
                break
        best = best or (found[0] if found else None)
        if trace is not None:
            trace.append({"step": "3_选取最合适链接", "prompt": prompt[:400], "model_response": text[:500], "url_picked": best})
        return best
    except Exception as e:
        if trace is not None:
            trace.append({"step": "3_选取最合适链接", "prompt": prompt[:400], "error": str(e)})
        return None


def _guess_platform_from_title(title: str) -> str:
    """标题中文含量高时倾向 B 站，英文多时倾向 YouTube"""
    if not title or len(title) < 3:
        return "未知"
    chinese = sum(1 for c in title if "\u4e00" <= c <= "\u9fff")
    if chinese / len(title) > 0.3:
        return "B站"
    return "YouTube"


def _make_search_url(platform: str, title: str, creator: str) -> str:
    """根据平台和关键词构造搜索 URL"""
    pl = (platform or "").lower()
    q = f"{title} {creator}".strip() or "video"
    q_enc = quote(q)
    if "youtube" in pl or "油管" in pl:
        return f"https://www.youtube.com/results?search_query={q_enc}"
    if "b站" in pl or "bilibili" in pl or "哔哩" in pl:
        return f"https://search.bilibili.com/all?keyword={q_enc}"
    if "小红书" in pl:
        return f"https://www.xiaohongshu.com/search_result?keyword={q_enc}"
    if "抖音" in pl or "douyin" in pl:
        return f"https://www.douyin.com/search/{q_enc}"
    return f"https://www.google.com/search?q={q_enc}"
