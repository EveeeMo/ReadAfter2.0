"""消息处理逻辑（异步执行）"""
import random
from app.feishu.bot import get_image_for_vision, reply_message

_SUCCESS_EMOJI = ("📚", "✨", "🎉", "📌", "✅", "💾", "🔖", "📝")


def _success_reply(chat_id: str, msg_id: str, text: str, chat_type: str = "", open_id: str = "") -> None:
    emoji = random.choice(_SUCCESS_EMOJI)
    reply_message(chat_id, msg_id, f"已保存～ {emoji}\n{text}", chat_type=chat_type, open_id=open_id)
from app.feishu.bitable import add_record
from app.services.link_parser import extract_metadata
from app.services.image_parser import analyze_image
from app.services.rag import search_and_answer, add_to_index


def handle_url(chat_id: str, msg_id: str, url: str, extra: str = "", chat_type: str = "", open_id: str = "") -> None:
    """处理链接：解析并写入飞书表格。extra 为用户在链接前粘贴的文案，用于补全标题/摘要"""
    meta = extract_metadata(url)
    if meta.get("error"):
        reply_message(chat_id, msg_id, f"链接解析失败：{meta['error']}", chat_type=chat_type, open_id=open_id)
        return

    title = meta.get("title", url)
    summary = meta.get("summary", "")
    # 用户附带文案时，优先用于标题/摘要（尤其是微信、小红书等抓取失败的情况）
    if extra:
        extra_clean = extra.strip()[:500]
        if "需手动补充" in summary or title in ("微信公众号文章", "小红书内容"):
            title = extra_clean[:200] if len(extra_clean) > 15 else title
        if "需手动补充" in summary or "（内容需手动补充）" in summary or not summary:
            summary = extra_clean[:500] or summary

    try:
        rec = add_record(
            content=title,
            summary=summary,
            author=meta.get("author", ""),
            platform=meta.get("platform", ""),
            publish_date=meta.get("publish_date", ""),
            full_text=meta.get("full_text", "")[:10000],
            source_type="链接",
            content_url=url,
        )
        rid = rec.get("record", {}).get("record_id")
        if rid:
            add_to_index(rid, meta.get("title", ""), meta.get("full_text", "")[:8000])
        _success_reply(chat_id, msg_id, f"已记录：{meta.get('title', url)[:50]}...", chat_type, open_id)
    except Exception as e:
        reply_message(chat_id, msg_id, f"保存失败：{str(e)}", chat_type=chat_type, open_id=open_id)


def handle_image(chat_id: str, msg_id: str, image_key: str, chat_type: str = "", open_id: str = "") -> None:
    """处理图片：识别链接并逐个解析入库；若无链接则提取视频信息并入库"""
    try:
        img_data = get_image_for_vision(image_key)
    except Exception as e:
        reply_message(chat_id, msg_id, f"获取图片失败：{str(e)}", chat_type=chat_type, open_id=open_id)
        return

    result = analyze_image(img_data)
    urls = result["urls"]
    fallback = result.get("fallback")

    if not urls and fallback:
        try:
            from app.services.image_parser import find_best_video_url

            best_url = find_best_video_url(
                fallback.get("platform", ""),
                fallback.get("title", ""),
                fallback.get("channel", ""),
            )
            url_to_use = best_url or fallback.get("search_url", "")
            meta = extract_metadata(url_to_use)
            if meta.get("error"):
                meta = {
                    "title": fallback["title"],
                    "summary": f"频道: {fallback['channel']} | 平台: {fallback['platform']}",
                    "author": fallback.get("channel", ""),
                    "platform": fallback.get("platform", ""),
                    "full_text": fallback.get("title", ""),
                }
            rec = add_record(
                content=meta.get("title", fallback["title"]),
                summary=meta.get("summary", ""),
                author=meta.get("author", ""),
                platform=meta.get("platform", ""),
                publish_date=meta.get("publish_date", ""),
                full_text=meta.get("full_text", "")[:10000],
                source_type="图片识别",
                content_url=url_to_use,
            )
            rid = rec.get("record", {}).get("record_id")
            if rid:
                add_to_index(rid, meta.get("title", ""), meta.get("full_text", "")[:8000])
            t = meta.get("title", fallback["title"])[:40]
            _success_reply(chat_id, msg_id, f"已根据截图选取链接并入库：{t}...", chat_type, open_id)
        except Exception as e:
            reply_message(chat_id, msg_id, f"保存失败：{str(e)}", chat_type=chat_type, open_id=open_id)
        return

    if not urls:
        reply_message(chat_id, msg_id, "未在图片中识别到链接或视频信息", chat_type=chat_type, open_id=open_id)
        return

    done = 0
    for url in urls[:5]:
        try:
            meta = extract_metadata(url)
            if meta.get("error"):
                continue
            add_record(
                content=meta.get("title", url),
                summary=meta.get("summary", ""),
                author=meta.get("author", ""),
                platform=meta.get("platform", ""),
                publish_date=meta.get("publish_date", ""),
                full_text=meta.get("full_text", "")[:10000],
                source_type="图片识别",
                content_url=url,
            )
            done += 1
        except Exception:
            continue
    _success_reply(chat_id, msg_id, f"从图片识别到 {len(urls)} 个链接，已记录 {done} 条", chat_type, open_id)


def handle_question(chat_id: str, msg_id: str, question: str, chat_type: str = "", open_id: str = "") -> None:
    """处理提问：RAG 检索并回答"""
    try:
        ans = search_and_answer(question)
        reply_message(chat_id, msg_id, ans[:2000], chat_type=chat_type, open_id=open_id)
    except Exception as e:
        reply_message(chat_id, msg_id, f"回答失败：{str(e)}", chat_type=chat_type, open_id=open_id)
