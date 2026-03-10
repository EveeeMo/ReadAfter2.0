"""消息处理逻辑（异步执行）"""
import random
import threading
import time
from app.feishu.bot import get_image_for_vision, reply_message

# 飞书可能重复推送同一事件，用 (msg_id, 内容键) 去重
_processing_lock = threading.Lock()
_processing_keys: set[str] = set()


def _claim_processing(msg_id: str, content_key: str = "") -> bool:
    """ content_key: 同一条消息内的不同内容用不同 key，如 url 或 image_key"""
    key = f"{msg_id}:{content_key}" if content_key else msg_id
    with _processing_lock:
        if key in _processing_keys:
            return False
        _processing_keys.add(key)
        if len(_processing_keys) > 500:
            _processing_keys.clear()
        return True


def _release_processing(msg_id: str, content_key: str = "") -> None:
    key = f"{msg_id}:{content_key}" if content_key else msg_id
    with _processing_lock:
        _processing_keys.discard(key)
from app.feishu.bitable import add_record, find_record_by_content_url
from app.services.link_parser import extract_metadata, _fetch_video_title as _fetch_video_title_from_url
from app.services.image_parser import analyze_image
from app.services.rag import search_and_answer, add_to_index
from app.services.summary_service import generate_summary

_SUCCESS_EMOJI = ("📚", "✨", "🎉", "📌", "✅", "💾", "🔖", "📝")

# 摘要处理过程追踪（供调试）
SUMMARY_TRACE: list[dict] = []

# 各步骤耗时（秒），供 /webhook/feishu/debug 展示
TIMING_BREAKDOWN: list[dict] = []


def _preview(s: str, max_len: int) -> str:
    s = (s or "").strip()
    if len(s) <= max_len:
        return s or "(空)"
    return s[:max_len] + "..."


def _success_reply(chat_id: str, msg_id: str, text: str, elapsed: float, chat_type: str = "", open_id: str = "", timing: list | None = None) -> None:
    """timing: 分步耗时列表，传入时用传入的，否则用全局 TIMING_BREAKDOWN（避免并发覆盖）"""
    emoji = random.choice(_SUCCESS_EMOJI)
    tlist = timing if timing is not None else TIMING_BREAKDOWN
    timing_str = ""
    if tlist:
        parts = []
        for t in tlist:
            lbl = t.get("short_desc") or t.get("desc") or "?"
            sec = t.get("elapsed", 0)
            if isinstance(sec, (int, float)):
                parts.append(f"{lbl}{sec}s")
        if parts:
            timing_str = " " + " ".join(parts)
    lines = [f"已保存～ {emoji}", text, f"⏱ 总耗时 {elapsed:.1f}s{timing_str}"]
    reply_message(chat_id, msg_id, "\n".join(lines), chat_type=chat_type, open_id=open_id)


def handle_url(chat_id: str, msg_id: str, url: str, extra: str = "", chat_type: str = "", open_id: str = "") -> None:
    """处理链接：解析并写入飞书表格。extra 为用户在链接前粘贴的文案，用于补全标题/摘要"""
    global SUMMARY_TRACE, TIMING_BREAKDOWN
    if not _claim_processing(msg_id, url):
        return  # 飞书重复推送或同消息多链接已处理过该 url
    try:
        _handle_url_impl(chat_id, msg_id, url, extra, chat_type, open_id)
    finally:
        _release_processing(msg_id, url)


def _handle_url_impl(chat_id: str, msg_id: str, url: str, extra: str = "", chat_type: str = "", open_id: str = "") -> None:
    global SUMMARY_TRACE, TIMING_BREAKDOWN
    t0 = time.time()
    SUMMARY_TRACE.clear()
    TIMING_BREAKDOWN.clear()

    t1 = time.time()
    meta = extract_metadata(url)
    TIMING_BREAKDOWN.append({"step": "1_extract_metadata", "desc": "抓取链接HTML并解析", "short_desc": "抓取", "elapsed": round(time.time() - t1, 2)})
    if meta.get("error"):
        elapsed = time.time() - t0
        reply_message(chat_id, msg_id, f"链接解析失败：{meta['error']}\n⏱ 耗时 {elapsed:.1f}s", chat_type=chat_type, open_id=open_id)
        return

    title = meta.get("title", url)
    raw_summary = meta.get("summary", "")
    full_text = meta.get("full_text", "")

    if extra:
        extra_clean = extra.strip()[:800]
        if "需手动补充" in raw_summary or title in ("微信公众号文章", "小红书内容"):
            title = extra_clean[:200] if len(extra_clean) > 15 else title
        if "需手动补充" in raw_summary or "（内容需手动补充）" in raw_summary or not raw_summary:
            full_text = full_text or extra_clean  # 抓取失败时，用 extra 作为「全文」

    # ========== 工作流：1.获取全文 2.AI总结 3.替换并发送 ==========
    SUMMARY_TRACE.append({
        "step": 1,
        "name": "1. 获取全文",
        "action": "从链接抓取或用户粘贴得到待总结的全文",
        "result": {
            "全文来源": "抓取" if meta.get("full_text") else ("用户粘贴" if full_text else "无"),
            "全文长度": len(full_text or ""),
            "全文预览": _preview(full_text or "", 150),
        },
    })

    summary = raw_summary  # 默认用原始摘要
    ai_summary = ""
    if full_text and len(full_text.strip()) >= 50:
        t2 = time.time()
        ai_summary = generate_summary(full_text, title)
        TIMING_BREAKDOWN.append({"step": "2_generate_summary", "desc": "AI 总结全文", "short_desc": "AI总结", "elapsed": round(time.time() - t2, 2)})
        SUMMARY_TRACE.append({
            "step": 2,
            "name": "2. 用 AI 总结全文生成新摘要",
            "action": "调用 generate_summary_unified（与调试页同一实现），gemini-2.5-pro / deepseek 兜底",
            "result": {
                "输入长度": len(full_text),
                "新摘要长度": len(ai_summary or ""),
                "新摘要内容": ai_summary or "(AI 返回空)",
            },
        })
        if ai_summary:
            summary = ai_summary
    else:
        SUMMARY_TRACE.append({
            "step": 2,
            "name": "2. 用 AI 总结全文生成新摘要",
            "action": "全文不足 50 字，跳过 AI 步骤",
            "result": {"全文长度": len(full_text or ""), "原因": "无可总结的全文"},
        })

    SUMMARY_TRACE.append({
        "step": 3,
        "name": "3. 替换并发送飞书",
        "action": "用新摘要替代原摘要，写入多维表格「摘要」字段",
        "result": {
            "原摘要长度": len(raw_summary),
            "最终摘要": "AI 新摘要" if ai_summary else "原摘要",
            "最终摘要长度": len(summary),
            "最终摘要内容": _preview(summary or "", 250),
        },
    })

    # 链接去重：同链接近期已记录则跳过，避免重复入库
    t3 = time.time()
    existing = find_record_by_content_url(url)
    TIMING_BREAKDOWN.append({"step": "3_find_record", "desc": "去重查询飞书表格", "short_desc": "去重", "elapsed": round(time.time() - t3, 2)})
    if existing:
        SUMMARY_TRACE.append({
            "step": "3a",
            "name": "3a. 链接去重",
            "action": "检测到该链接已在表格中存在，跳过写入",
            "result": {"跳过原因": "同链接已记录"},
        })
        elapsed = time.time() - t0
        reply_message(chat_id, msg_id, f"该链接已记录过，已跳过～ ⏱ 耗时 {elapsed:.1f}s", chat_type=chat_type, open_id=open_id)
        return

    try:
        t4 = time.time()
        rec = add_record(
            content=title,
            summary=summary,
            author=meta.get("author", ""),
            platform=meta.get("platform", ""),
            publish_date=meta.get("publish_date", ""),
            full_text=(full_text or meta.get("full_text", ""))[:10000],
            source_type="链接",
            content_url=url,
        )
        TIMING_BREAKDOWN.append({"step": "4_add_record", "desc": "写入飞书表格", "short_desc": "写表", "elapsed": round(time.time() - t4, 2)})
        rid = rec.get("record", {}).get("record_id")
        if rid:
            t5 = time.time()
            add_to_index(rid, title, (full_text or meta.get("full_text", ""))[:8000])
            TIMING_BREAKDOWN.append({"step": "5_add_to_index", "desc": "ChromaDB+Embedding 建索引", "short_desc": "建索引", "elapsed": round(time.time() - t5, 2)})
        elapsed = time.time() - t0
        _success_reply(chat_id, msg_id, f"已记录：{meta.get('title', url)[:50]}...", elapsed, chat_type, open_id, timing=list(TIMING_BREAKDOWN))
    except Exception as e:
        elapsed = time.time() - t0
        reply_message(chat_id, msg_id, f"保存失败：{str(e)}\n⏱ 耗时 {elapsed:.1f}s", chat_type=chat_type, open_id=open_id)


def handle_image(chat_id: str, msg_id: str, image_key: str, chat_type: str = "", open_id: str = "") -> None:
    """处理图片：识别链接并逐个解析入库；若无链接则提取视频信息并入库"""
    if not _claim_processing(msg_id, image_key):
        return  # 飞书重复推送，已有任务在处理
    try:
        _handle_image_impl(chat_id, msg_id, image_key, chat_type, open_id)
    finally:
        _release_processing(msg_id, image_key)


def _handle_image_impl(chat_id: str, msg_id: str, image_key: str, chat_type: str = "", open_id: str = "") -> None:
    global TIMING_BREAKDOWN
    t0 = time.time()
    TIMING_BREAKDOWN.clear()
    try:
        t1 = time.time()
        img_data = get_image_for_vision(image_key, message_id=msg_id)
        TIMING_BREAKDOWN.append({"step": "1_get_image", "desc": "从飞书获取图片", "short_desc": "取图", "elapsed": round(time.time() - t1, 2)})
    except Exception as e:
        elapsed = time.time() - t0
        reply_message(chat_id, msg_id, f"获取图片失败：{str(e)}\n⏱ 耗时 {elapsed:.1f}s", chat_type=chat_type, open_id=open_id)
        return

    t2 = time.time()
    result = analyze_image(img_data)
    TIMING_BREAKDOWN.append({"step": "2_analyze_image", "desc": "多模态识别链接/视频信息", "short_desc": "识图", "elapsed": round(time.time() - t2, 2)})
    urls = result["urls"]
    fallback = result.get("fallback")

    if not urls and fallback:
        try:
            from app.services.image_parser import find_best_video_url

            t3 = time.time()
            best_url = find_best_video_url(
                fallback.get("platform", ""),
                fallback.get("title", ""),
                fallback.get("channel", ""),
            )
            TIMING_BREAKDOWN.append({"step": "3_find_best_url", "desc": "联网搜索视频链接", "short_desc": "搜链", "elapsed": round(time.time() - t3, 2)})
            url_to_use = best_url or fallback.get("search_url", "")
            t4 = time.time()
            meta = extract_metadata(url_to_use)
            TIMING_BREAKDOWN.append({"step": "4_extract_metadata", "desc": "抓取链接", "short_desc": "抓取", "elapsed": round(time.time() - t4, 2)})
            if meta.get("error"):
                # 抓取失败时，对 YouTube 等尝试 oEmbed 获取真实标题
                title_from_api = _fetch_video_title_from_url(url_to_use)
                meta = {
                    "title": title_from_api or fallback["title"],
                    "summary": f"频道: {fallback['channel']} | 平台: {fallback['platform']}",
                    "author": fallback.get("channel", ""),
                    "platform": fallback.get("platform", ""),
                    "full_text": fallback.get("title", ""),
                    "publish_date": "",  # 由 extract_metadata 从链接抓取
                }
            # 图片场景：摘要直接用链接标题，不调用 AI 总结（省时且更准确）
            summary = meta.get("title", fallback["title"])
            rec = add_record(
                content=meta.get("title", fallback["title"]),
                summary=summary,
                author=meta.get("author", ""),
                platform=meta.get("platform", ""),
                publish_date=meta.get("publish_date", ""),  # 统一从链接抓取
                full_text=meta.get("full_text", "")[:10000],
                source_type="图片识别",
                content_url=url_to_use,
            )
            rid = rec.get("record", {}).get("record_id")
            if rid:
                t6 = time.time()
                add_to_index(rid, meta.get("title", ""), meta.get("full_text", "")[:8000])
                TIMING_BREAKDOWN.append({"step": "6_add_to_index", "desc": "ChromaDB 建索引", "short_desc": "建索引", "elapsed": round(time.time() - t6, 2)})
            t = meta.get("title", fallback["title"])[:40]
            elapsed = time.time() - t0
            _success_reply(chat_id, msg_id, f"已根据截图选取链接并入库：{t}...", elapsed, chat_type, open_id, timing=list(TIMING_BREAKDOWN))
        except Exception as e:
            elapsed = time.time() - t0
            reply_message(chat_id, msg_id, f"保存失败：{str(e)}\n⏱ 耗时 {elapsed:.1f}s", chat_type=chat_type, open_id=open_id)
        return

    if not urls:
        elapsed = time.time() - t0
        reply_message(chat_id, msg_id, f"未在图片中识别到链接或视频信息\n⏱ 耗时 {elapsed:.1f}s", chat_type=chat_type, open_id=open_id)
        return

    done = 0
    for url in urls[:5]:
        try:
            t_meta = time.time()
            meta = extract_metadata(url)
            if not TIMING_BREAKDOWN or TIMING_BREAKDOWN[-1].get("step") != "3_extract_metadata":
                TIMING_BREAKDOWN.append({"step": "3_extract_metadata", "desc": "抓取链接(每个)", "short_desc": "抓取", "elapsed": round(time.time() - t_meta, 2)})
            if meta.get("error"):
                continue
            # 图片场景：摘要直接用链接标题，不调用 AI 总结
            summary = meta.get("title", url)
            add_record(
                content=meta.get("title", url),
                summary=summary,
                author=meta.get("author", ""),
                platform=meta.get("platform", ""),
                publish_date=meta.get("publish_date", ""),  # 统一从链接抓取
                full_text=meta.get("full_text", "")[:10000],
                source_type="图片识别",
                content_url=url,
            )
            done += 1
        except Exception:
            continue
    elapsed = time.time() - t0
    _success_reply(chat_id, msg_id, f"从图片识别到 {len(urls)} 个链接，已记录 {done} 条", elapsed, chat_type, open_id, timing=list(TIMING_BREAKDOWN))


def handle_question(chat_id: str, msg_id: str, question: str, chat_type: str = "", open_id: str = "") -> None:
    """处理提问：RAG 检索并回答"""
    t0 = time.time()
    try:
        ans = search_and_answer(question)
        elapsed = time.time() - t0
        reply_message(chat_id, msg_id, f"{ans[:2000]}\n⏱ 耗时 {elapsed:.1f}s", chat_type=chat_type, open_id=open_id)
    except Exception as e:
        elapsed = time.time() - t0
        reply_message(chat_id, msg_id, f"回答失败：{str(e)}\n⏱ 耗时 {elapsed:.1f}s", chat_type=chat_type, open_id=open_id)
