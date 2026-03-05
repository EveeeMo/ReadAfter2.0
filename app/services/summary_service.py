"""AI 摘要生成"""
import re
import time
import uuid
from app.config import AI_BUILDER_TOKEN, AI_BUILDER_BASE_URL


# 初始 Prompt 模板（供调试页展示，变量：{title} {text}）
INITIAL_PROMPT_TEMPLATE = """你是一个摘要专家，请阅读全文，用几句话概括文章核心，总字数不超过 300 字。
输入：
标题：{title}
正文：{text}

输出：
对文章的总结摘要，仅返回摘要本身即可。"""


# 典型「照抄」开头的新闻腔词汇，若摘要以此开头且正文前段也含，视为照抄
_COPY_PHRASE_STARTS = ("终于", "日前", "据悉", "据报道", "根据", "近日", "今天", "昨日", "刚刚")

def _looks_like_copy(summary: str, full_text: str, head_len: int = 18) -> bool:
    """检测摘要是否照抄正文开头"""
    if not summary or not full_text or len(summary) < 10:
        return False
    s = summary.strip().replace(" ", "")
    t = full_text[:300].replace(" ", "")
    # 摘要前 N 字若出现在正文前 300 字内，视为照抄
    if len(s) >= head_len and s[:head_len] in t:
        return True
    # 摘要以新闻腔开头且正文前 100 字也含该词
    for phrase in _COPY_PHRASE_STARTS:
        if s.startswith(phrase) and phrase in full_text[:120]:
            return True
    return False


# 后处理：需去除的引言/标题，只保留摘要正文（按长度降序，优先匹配长的）
_STRIP_PREFIXES = (
    "基于您提供的文章内容，我来为您撰写摘要：\n\n**文章核心摘要：**\n\n",
    "基于您提供的文章内容，我来为您撰写摘要：\n\n**文章核心摘要：**\n",
    "基于您提供的文章内容，我来为您撰写摘要：\n\n**文章核心摘要：**",
    "基于您提供的文章内容，我来为您撰写摘要：\n\n",
    "基于您提供的文章内容，我来为您撰写摘要：",
    "我来为您撰写摘要：\n\n",
    "我来为您撰写摘要：",
    "**文章核心摘要：**\n\n",
    "**文章核心摘要：**\n",
    "**文章核心摘要：**",
    "**摘要：**\n\n",
    "**摘要：**",
    "文章核心摘要：\n\n",
    "文章核心摘要：\n",
    "文章核心摘要：",
    "以下是对文章的摘要：\n\n",
    "以下是对文章的摘要：",
    "以下为摘要：\n\n",
    "以下为摘要：",
    "摘要：",
    "摘要:",
    "概括：",
    "概括:",
)


def _strip_summary_prefix(text: str) -> str:
    """去除 AI 返回的引言、标题等，只保留摘要正文"""
    s = text.strip()
    changed = True
    while changed:
        changed = False
        for p in _STRIP_PREFIXES:
            if s.startswith(p):
                s = s[len(p):].strip()
                changed = True
                break
    return s


def _fallback_short_summary(text: str, max_chars: int = 180) -> str:
    """AI 失败时的兜底：按句号截断，避免前几百字直接照搬"""
    if not text or len(text.strip()) < 20:
        return ""
    s = re.sub(r"\s+", " ", text.strip())[:max_chars * 2]
    # 在 max_chars 内找最后一个句号
    cut = s[:max_chars]
    for sep in ("。", "！", "？", ".", "!", "?", "；", "\n"):
        pos = cut.rfind(sep)
        if pos > 50:
            return cut[: pos + 1].strip()
    return cut.rstrip("，、") + "…"


def _call_ai_api(prompt: str, model: str = "gemini-2.5-pro") -> tuple[str, dict]:
    """调用 AI API，返回 (raw_content, api_debug)"""
    raw = ""
    debug = {}
    try:
        from openai import OpenAI
        client = OpenAI(api_key=AI_BUILDER_TOKEN, base_url=AI_BUILDER_BASE_URL)
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=500,
            temperature=0.5,
        )
        debug["choices_len"] = len(resp.choices) if resp.choices else 0
        if resp.choices:
            c0, msg = resp.choices[0], resp.choices[0].message
            debug["finish_reason"] = getattr(c0, "finish_reason", None)
            content = getattr(msg, "content", None)
            if isinstance(content, list):
                raw = "".join(p.get("text", p.get("content", str(p))) for p in content if isinstance(p, dict)).strip()
            elif content:
                raw = str(content).strip()
        if not raw and resp.choices:
            try:
                debug["message_dump"] = resp.choices[0].message.model_dump() if hasattr(resp.choices[0].message, "model_dump") else str(resp.choices[0].message)[:500]
            except Exception:
                pass
    except Exception as e:
        raw = f"[API 调用失败: {e}]"
        debug["error"] = str(e)
    return raw, debug


def generate_summary_unified(
    full_text: str,
    title: str = "",
    prompt_template: str | None = None,
    trace: list | None = None,
    return_debug: bool = False,
) -> str | tuple[str, dict]:
    """
    与调试页 /debug/summary-prompt 完全相同的摘要逻辑。
    generate_summary 与 debug 端点均调用此函数，保证同一套实现。
    """
    tmpl = prompt_template or INITIAL_PROMPT_TEMPLATE
    text = (full_text or "").strip()[:10000]
    if len(text) < 50:
        return ""
    if not AI_BUILDER_TOKEN:
        return _fallback_short_summary(text, 180)

    try:
        prompt_sent = tmpl.format(title=title or "(无)", text=text)
    except KeyError:
        return _fallback_short_summary(text, 180)

    nonce = f"{int(time.time()*1000)}_{uuid.uuid4().hex[:8]}"
    prompt_with_nonce = prompt_sent + f"\n\n[req:{nonce}]"

    raw_response, api_debug = _call_ai_api(prompt_with_nonce)
    if not raw_response or len(raw_response.strip()) < 10:
        raw_response2, retry_debug = _call_ai_api(prompt_with_nonce, model="deepseek")
        if raw_response2 and len(raw_response2.strip()) >= 10:
            raw_response = raw_response2
            api_debug["retry_model"] = "deepseek"
            api_debug["retry_success"] = True
        else:
            api_debug["retry_model"] = "deepseek"
            api_debug["retry_error"] = retry_debug.get("error", "")

    used_fallback = False
    if raw_response and len(raw_response.strip()) >= 10:
        summary = _strip_summary_prefix(raw_response)
    else:
        summary = _fallback_short_summary(text, 180)
        used_fallback = True

    if trace is not None:
        trace.append({
            "step": 0,
            "name": "0. 输入",
            "desc": "Prompt 模板、全文、标题",
            "input": {"prompt_template_len": len(tmpl), "full_text_len": len(text), "title": title or "(无)"},
        })
        trace.append({
            "step": 1,
            "name": "1. 替换占位符",
            "desc": "将模板中的 {title}、{text} 替换为实际值",
            "input": {"title": title or "(无)", "text_len": len(text)},
            "output_preview": prompt_sent[:300] + "..." if len(prompt_sent) > 300 else prompt_sent,
            "output_len": len(prompt_sent),
            "warn": "⚠️ prompt 过短，请检查模板是否包含 {text} 占位符" if len(prompt_sent) < 100 else None,
        })
        trace.append({
            "step": 2,
            "name": "2. 防缓存标识",
            "desc": "在 prompt 末尾附加唯一 nonce",
            "output_preview": f"...[req:{nonce}]",
        })
        trace.append({
            "step": 3,
            "name": "3. 调用 AI API",
            "desc": "POST → gemini-2.5-pro，messages=[user: prompt], temperature=0.5",
            "input": {"model": "gemini-2.5-pro", "prompt_len": len(prompt_with_nonce), "base_url": AI_BUILDER_BASE_URL},
            "output_raw": raw_response if raw_response else "[空] API 返回的 content 为空",
            "api_debug": api_debug,
        })
        trace.append({
            "step": 4,
            "name": "4. 后处理",
            "desc": "去除引言、标题等前缀，只保留摘要正文" + ("（⚠️ API 返回为空，已使用兜底）" if used_fallback else ""),
            "input_raw": raw_response,
            "output": summary,
            "used_fallback": used_fallback,
        })
    if return_debug and trace is not None:
        return summary, {
            "raw_api_response": raw_response or "[空]",
            "used_fallback": used_fallback,
            "prompt_sent": prompt_sent,
            "trace": trace,
        }
    return summary


def generate_summary_with_prompt(full_text: str, prompt: str, cache_bust: bool = True) -> str:
    """用指定 prompt 调用 AI 生成摘要（供调试页使用）
    cache_bust: 在 prompt 末尾附加唯一标识，避免响应被缓存导致每次返回相同结果
    """
    if not full_text or len(full_text.strip()) < 50:
        return ""
    if not AI_BUILDER_TOKEN:
        return _fallback_short_summary(full_text, 180)
    text = full_text.strip()[:10000]
    if cache_bust:
        nonce = f"{int(time.time()*1000)}_{uuid.uuid4().hex[:8]}"
        prompt = prompt + f"\n\n[req:{nonce}]"
    try:
        from openai import OpenAI
        client = OpenAI(api_key=AI_BUILDER_TOKEN, base_url=AI_BUILDER_BASE_URL)
        resp = client.chat.completions.create(
            model="gemini-2.5-pro",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=500,
            temperature=0.5,
        )
        summary = (resp.choices[0].message.content or "").strip()
        if not summary or len(summary) < 10:
            return _fallback_short_summary(full_text, 180)
        return _strip_summary_prefix(summary)
    except Exception:
        pass
    return _fallback_short_summary(full_text, 180)


def generate_summary(full_text: str, title: str = "", max_chars: int = 120) -> str:
    """
    对正文生成摘要。内部调用 generate_summary_unified，与调试页同一套实现。
    """
    return generate_summary_unified(full_text, title, prompt_template=INITIAL_PROMPT_TEMPLATE, trace=None)
