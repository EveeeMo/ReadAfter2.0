"""ReadAfter2.0 - FastAPI 入口"""
import os
from contextlib import asynccontextmanager
import httpx
from fastapi import FastAPI, File, Form, Request, UploadFile, BackgroundTasks
from fastapi.responses import JSONResponse, HTMLResponse

from app.config import PORT
from app.feishu.bot import parse_event
from app.handlers.message_handler import handle_url, handle_image, handle_question


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    # 可选：关闭时的清理


app = FastAPI(title="ReadAfter2.0", lifespan=lifespan)


@app.get("/")
def root():
    return {"service": "ReadAfter2.0", "status": "ok"}


@app.get("/health")
def health():
    return {"status": "ok"}


# ========== 本地测试用接口（部署前验证） ==========

@app.get("/test/link")
def test_link(url: str = "", text: str = ""):
    """测试链接解析 + 写入飞书表格。
    - 纯链接: ?url=https://example.com
    - 小红书格式: ?text=跨境支付必考... http://xhslink.com/o/xxx 复制后打开【小红书】
    """
    import re

    if not url and not text:
        return {"ok": False, "error": "请提供 url 或 text 参数"}
    if text and not url:
        match = re.search(r"https?://[^\s\)\]\"\']+", text)
        if match:
            url = match.group(0).rstrip(".,;:!?）】")
            extra = text[: match.start()].strip()[:500]
        else:
            return {"ok": False, "error": "未在文本中找到链接"}
    else:
        extra = ""

    try:
        from app.services.link_parser import extract_metadata
        from app.feishu.bitable import add_record
        from app.services.rag import add_to_index

        meta = extract_metadata(url)
        if meta.get("error"):
            return {"ok": False, "error": meta["error"]}

        title = meta.get("title", url)
        summary = meta.get("summary", "")
        if extra and ("需手动补充" in summary or title in ("微信公众号文章", "小红书内容")):
            title = extra[:200] if len(extra) > 15 else title
        if extra and ("需手动补充" in summary or "（内容需手动补充）" in summary or not summary):
            summary = extra[:500] or summary

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
            add_to_index(rid, title, meta.get("full_text", "")[:8000] or summary)
        return {"ok": True, "title": title, "record_id": rid}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.get("/test/image/page", response_class=HTMLResponse)
def test_image_page():
    """图片测试页：上传含链接的截图"""
    return """
    <!DOCTYPE html>
    <html><head><meta charset="utf-8"><title>图片识别测试</title></head>
    <body style="font-family:sans-serif;max-width:500px;margin:40px auto;padding:20px">
    <h2>ReadAfter2.0 图片识别测试</h2>
    <p>上传一张含链接的截图（如小红书分享图、视频截图等），将识别其中的链接并写入飞书表格。</p>
    <form action="/test/image/upload" method="post" enctype="multipart/form-data">
      <input type="file" name="file" accept="image/*" required style="margin:10px 0"><br>
      <label><input type="checkbox" name="trace" value="1"> 显示每步过程（trace=1）</label><br>
      <button type="submit" style="padding:8px 16px;margin-top:8px">识别并入库</button>
    </form>
    <p style="color:#666;font-size:14px">带 trace：<a href="/test/image?image_url=图片URL&trace=1">/test/image?image_url=xxx&trace=1</a></p>
    </body></html>
    """


@app.get("/test/image/debug")
def test_image_debug(image_url: str = ""):
    """调试：查看图片分析每步的详细过程（不写入表格）"""
    if not image_url:
        return {"ok": False, "error": "请提供 image_url"}
    try:
        from app.services.image_parser import analyze_image
        steps = []
        result = analyze_image(image_url, trace=steps)
        return {"ok": True, "result": result, "trace": steps}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.get("/test/image")
def test_image(image_url: str = "", trace: int = 0):
    """测试图片识别：传入含链接的截图 URL，识别并入库。
    trace=1 时返回每步的详细过程，便于定位问题。
    例: GET /test/image?image_url=xxx 或 /test/image?image_url=xxx&trace=1
    """
    if not image_url:
        return {"ok": False, "error": "请提供 image_url 参数（图片的公开 URL）"}

    steps = [] if trace else None
    try:
        from app.services.image_parser import analyze_image, find_best_video_url
        from app.services.link_parser import extract_metadata
        from app.feishu.bitable import add_record
        from app.services.rag import add_to_index

        result = analyze_image(image_url, trace=steps)
        urls = result["urls"]
        fallback = result.get("fallback")

        # 无链接时，若有 fallback：选取最合适视频链接，再走标准链接流程
        if not urls and fallback:
            try:
                best_url = find_best_video_url(
                    fallback.get("platform", ""),
                    fallback.get("title", ""),
                    fallback.get("channel", ""),
                    trace=steps,
                )
                url_to_use = best_url or fallback.get("search_url", "")
                meta = extract_metadata(url_to_use)
                if meta.get("error"):
                    meta = {
                        "title": fallback["title"],
                        "summary": f"频道: {fallback['channel']} | 平台: {fallback['platform']}",
                        "author": fallback.get("channel", ""),
                        "platform": fallback.get("platform", "图片识别"),
                        "full_text": fallback.get("title", ""),
                    }
                if steps is not None:
                    steps.append({
                        "step": "4_解析链接元数据",
                        "url": url_to_use[:100],
                        "meta": {k: (str(v)[:200] if v else "") for k, v in meta.items()},
                    })

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
                if steps is not None:
                    steps.append({"step": "5_写入表格", "record_id": rid, "status": "ok"})
                out = {
                    "ok": True,
                    "urls_found": 0,
                    "message": "已根据截图选取链接并入库",
                    "recorded": 1,
                    "details": [{"title": meta.get("title"), "url": url_to_use[:80]}],
                }
                if steps is not None:
                    out["trace"] = steps
                return out
            except Exception as e:
                if steps is not None:
                    steps.append({"step": "error", "message": str(e)})
                return {"ok": False, "error": str(e), "trace": steps}

        if not urls:
            out = {"ok": True, "urls_found": 0, "message": "未在图片中识别到链接或视频信息"}
            if steps is not None:
                out["trace"] = steps
            return out

        done = []
        for url in urls[:5]:
            try:
                meta = extract_metadata(url)
                if meta.get("error"):
                    continue
                rec = add_record(
                    content=meta.get("title", url),
                    summary=meta.get("summary", ""),
                    author=meta.get("author", ""),
                    platform=meta.get("platform", ""),
                    publish_date=meta.get("publish_date", ""),
                    full_text=meta.get("full_text", "")[:10000],
                    source_type="图片识别",
                    content_url=url,
                )
                rid = rec.get("record", {}).get("record_id")
                if rid:
                    add_to_index(rid, meta.get("title", ""), meta.get("full_text", "")[:8000])
                done.append({"url": url[:80], "title": meta.get("title", "")[:50]})
            except Exception:
                continue

        out = {"ok": True, "urls_found": len(urls), "recorded": len(done), "details": done}
        if steps is not None:
            out["trace"] = steps
        return out
    except Exception as e:
        return {"ok": False, "error": str(e), "trace": steps if steps else None}


@app.post("/test/image/upload")
def test_image_upload(
    file: UploadFile = File(..., description="含链接的截图"),
    trace: int = Form(0),
):
    """测试图片识别：上传截图文件，识别其中链接并入库。URL 加 ?trace=1 可查看每步过程"""
    import base64

    try:
        content = file.file.read()
        b64 = base64.b64encode(content).decode()
        data_url = f"data:{file.content_type or 'image/png'};base64,{b64}"
        return test_image(image_url=data_url, trace=trace)
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.get("/test/rag")
def test_rag(q: str):
    """测试 RAG 问答。例: GET /test/rag?q=总结一下最近收集的内容"""
    try:
        from app.services.rag import search_and_answer
        ans = search_and_answer(q)
        return {"ok": True, "answer": ans}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.get("/test/feishu/debug")
def test_feishu_debug():
    """调试：直接请求飞书 API，返回原始响应（不含 raise）"""
    from app.config import FEISHU_BITABLE_APP_TOKEN, FEISHU_BITABLE_TABLE_ID
    from app.feishu.auth import get_tenant_access_token

    try:
        token = get_tenant_access_token()
    except Exception as e:
        return {"step": "token", "error": str(e)}

    url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{FEISHU_BITABLE_APP_TOKEN}/tables/{FEISHU_BITABLE_TABLE_ID}/records"
    resp = httpx.get(url, params={"page_size": 3}, headers={"Authorization": f"Bearer {token}"}, timeout=15)
    return {
        "step": "bitable",
        "status_code": resp.status_code,
        "response_body": resp.json() if resp.headers.get("content-type", "").startswith("application/json") else resp.text[:1000],
    }


@app.get("/test/feishu")
def test_feishu():
    """测试飞书连接：获取 token 并读取表格前几条记录"""
    try:
        from app.feishu.auth import get_tenant_access_token
        token = get_tenant_access_token()
    except Exception as e:
        return {"ok": False, "step": "token", "error": str(e)}

    try:
        from app.feishu.bitable import list_records
        records = list_records(limit=3)
        return {
            "ok": True,
            "token_preview": f"{token[:10]}..." if token else "",
            "record_count": len(records),
            "sample": records[:1] if records else [],
        }
    except Exception as e:
        err = str(e)
        hint = ""
        if "99991672" in err or "bitable:app" in err:
            hint = "请在飞书开放平台为应用开通权限：bitable:app 和 base:record:create"
        elif "400" in err or "400 Bad Request" in err:
            hint = "请确认：1) 开通多维表格权限；2) 将应用添加为多维表格的协作者"
        return {"ok": False, "step": "bitable", "error": err[:500], "hint": hint}


# 用于调试：记录最近一次 webhook 请求（不含敏感内容）
_webhook_last: dict = {}


@app.get("/webhook/feishu/debug")
def webhook_debug():
    """查看是否收到飞书回调，便于排查"""
    return _webhook_last if _webhook_last else {"message": "尚未收到任何飞书回调请求"}


@app.post("/webhook/feishu")
async def feishu_webhook(request: Request, background_tasks: BackgroundTasks):
    """飞书事件回调：立即返回 200，后台异步处理"""
    import time
    body = await request.json()
    _webhook_last.clear()
    _webhook_last["time"] = time.strftime("%Y-%m-%d %H:%M:%S")
    _webhook_last["type"] = body.get("type", "")
    ev = body.get("event", {})
    _webhook_last["event_type"] = ev.get("type", "")
    _webhook_last["msg_type"] = ev.get("message", {}).get("message_type", "")
    _webhook_last["chat_id_preview"] = (ev.get("message", {}).get("chat_id", ""))[:20] + "..."

    parsed = parse_event(body)
    if parsed is None:
        return JSONResponse(content={})

    if parsed.get("type") == "url_verification":
        return JSONResponse(content={"challenge": parsed.get("challenge", "")})

    msg_type = parsed.get("type")
    chat_id = parsed.get("chat_id", "")
    msg_id = parsed.get("msg_id", "")
    content = parsed.get("content", "")

    if not chat_id or not msg_id:
        return JSONResponse(content={})

    # 同步回复「ping」「测试」，用于快速验证通道是否打通
    if msg_type == "text" and str(content).strip() in ("ping", "测试", "pong"):
        try:
            from app.feishu.bot import reply_message
            reply_message(chat_id, msg_id, "ReadAfter2.0 已收到～ ✅")
        except Exception as e:
            _webhook_last["reply_error"] = str(e)
        return JSONResponse(content={})

    if msg_type == "url":
        extra = parsed.get("extra", "")
        background_tasks.add_task(handle_url, chat_id, msg_id, content, extra)
    elif msg_type == "urls":
        extra = parsed.get("extra", "")
        for url in content:
            background_tasks.add_task(handle_url, chat_id, msg_id, url, extra)
    elif msg_type == "image":
        background_tasks.add_task(handle_image, chat_id, msg_id, content)
    elif msg_type == "text":
        background_tasks.add_task(handle_question, chat_id, msg_id, content)

    return JSONResponse(content={})


if __name__ == "__main__":
    port = int(os.getenv("PORT", str(PORT)))
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=port)
