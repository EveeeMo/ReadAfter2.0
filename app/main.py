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
                        "publish_date": "",  # 由 extract_metadata 从链接抓取
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
                    publish_date=meta.get("publish_date", ""),  # 统一从链接抓取
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
                if steps is not None:
                    steps.append({
                        "step": "4_解析链接元数据",
                        "url": url[:80],
                        "publish_date": meta.get("publish_date", ""),
                    })
                rec = add_record(
                    content=meta.get("title", url),
                    summary=meta.get("summary", ""),
                    author=meta.get("author", ""),
                    platform=meta.get("platform", ""),
                    publish_date=meta.get("publish_date", ""),  # 统一从链接抓取
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


@app.get("/test/summary")
def test_summary(text: str = "", title: str = ""):
    """测试摘要生成。例: /test/summary?text=很长的正文...&title=标题"""
    if not text or len(text) < 30:
        return {"ok": False, "error": "请提供 text 参数（至少 30 字）"}
    try:
        from app.services.summary_service import generate_summary
        s = generate_summary(text[:10000], title or "")
        return {"ok": True, "summary": s, "input_len": len(text)}
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


# 用于调试：记录最近一次 webhook 请求及后台任务状态
_webhook_last: dict = {}
_task_status: dict = {}  # 后台任务状态：started_at, done_at, error
# 去重：同一 message_id 在短时间内不重复发「已收到」（飞书可能重复推送 webhook）
_ack_msg_ids: dict = {}  # message_id -> timestamp


@app.get("/webhook/feishu/debug")
def webhook_debug():
    """查看是否收到飞书回调及后台任务状态"""
    out = dict(_webhook_last) if _webhook_last else {"message": "尚未收到任何飞书回调请求"}
    if _task_status:
        out["task"] = _task_status
    return out


def _render_trace_html(steps: list) -> str:
    """将 trace 渲染为 HTML"""
    if not steps:
        return """<!DOCTYPE html><html><head><meta charset="utf-8"><title>摘要流程追踪</title>
<style>body{font-family:system-ui;max-width:720px;margin:40px auto;padding:20px;background:#f5f5f5}
.empty{padding:40px;text-align:center;color:#666;background:#fff;border-radius:8px}</style></head>
<body><div class="empty">尚未处理任何链接。请向机器人发送一条链接后刷新此页。</div></body></html>"""
    html = """<!DOCTYPE html><html><head><meta charset="utf-8"><title>摘要流程追踪</title>
<style>
body{font-family:system-ui,-apple-system,sans-serif;max-width:720px;margin:40px auto;padding:20px;background:#f0f2f5;color:#1a1a1a}
h1{font-size:1.25rem;margin-bottom:24px;color:#333}
.card{background:#fff;border-radius:8px;padding:16px 20px;margin-bottom:12px;box-shadow:0 1px 3px rgba(0,0,0,.08);border-left:4px solid #1890ff}
.card h3{margin:0 0 8px;font-size:0.95rem;color:#1890ff}
.card .action{color:#666;font-size:0.9rem;margin-bottom:10px}
.card .result{background:#fafafa;padding:10px 12px;border-radius:6px;font-size:0.88rem;line-height:1.6}
.card .result p{margin:4px 0}
.card .result k{color:#666;font-weight:normal}
</style></head><body><h1>📋 摘要生成流程</h1>"""
    for s in steps:
        r = s.get("result", {})
        def _fmt(v):
            if v is True: return "是"
            if v is False: return "否"
            return v
        rows = "\n".join(f"<p><k>{k}:</k> {_fmt(v)}</p>" for k, v in r.items() if v is not None and v != "")
        if isinstance(r, dict) and not rows:
            rows = "<p>(无)</p>"
        html += f"""
<div class="card">
  <h3>步骤 {s.get('step')}：{s.get('name', '')}</h3>
  <div class="action">📌 {s.get('action', '')}</div>
  <div class="result">{rows}</div>
</div>"""
    html += "</body></html>"
    return html


@app.get("/debug/summary-trace", response_class=HTMLResponse)
def debug_summary_trace():
    """查看最近一次链接处理的摘要生成过程（可视化）"""
    from app.handlers.message_handler import SUMMARY_TRACE
    return _render_trace_html(SUMMARY_TRACE)


def _summary_prompt_debug_page() -> str:
    """Prompt 调试页：保留全文、保留初始 prompt，可修改"""
    import html
    import json
    from app.services.summary_service import INITIAL_PROMPT_TEMPLATE
    default_template = html.escape(INITIAL_PROMPT_TEMPLATE)
    default_template_json = html.escape(json.dumps(INITIAL_PROMPT_TEMPLATE))
    return f'''<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>摘要 Prompt 调试</title>
<style>
body{{font-family:system-ui,-apple-system,sans-serif;max-width:900px;margin:32px auto;padding:24px;background:#f5f7fa;color:#1a1a1a}}
h1{{font-size:1.35rem;margin-bottom:8px;color:#333}}
h2{{font-size:1rem;margin:24px 0 12px;color:#555;font-weight:600}}
.card{{background:#fff;border-radius:10px;padding:20px;margin-bottom:16px;box-shadow:0 1px 4px rgba(0,0,0,.06)}}
.card pre{{white-space:pre-wrap;word-break:break-word;font-size:0.88rem;line-height:1.6;margin:0;font-family:ui-monospace,monospace}}
textarea{{width:100%;min-height:180px;padding:12px;border:1px solid #ddd;border-radius:6px;font-size:0.9rem;line-height:1.5;resize:vertical}}
input[type=text]{{width:100%;padding:10px 12px;border:1px solid #ddd;border-radius:6px;font-size:0.9rem}}
button{{padding:10px 24px;background:#1890ff;color:#fff;border:none;border-radius:6px;font-size:0.95rem;cursor:pointer}}
button:hover{{background:#0d7de0}}
button:disabled{{background:#ccc;cursor:not-allowed}}
button.secondary{{background:#666}}
button.secondary:hover{{background:#555}}
#result{{margin-top:16px}}
#result .ok{{color:#52c41a}}
#result .err{{color:#ff4d4f}}
#result pre{{background:#fafafa;padding:12px;border-radius:6px;margin:8px 0}}
.hint{{font-size:0.8rem;color:#999;margin-top:6px}}
.flow{{margin:20px 0}}
.flow-step{{background:#f8fafc;border-left:4px solid #1890ff;padding:14px 16px;margin-bottom:12px;border-radius:0 8px 8px 0}}
.flow-step h4{{margin:0 0 6px;font-size:0.95rem;color:#1890ff}}
.flow-step .desc{{color:#666;font-size:0.85rem;margin-bottom:8px}}
.flow-step pre{{margin:0;font-size:0.8rem;background:#fff;padding:10px;border:1px solid #eee;max-height:120px;overflow:auto}}
.flow-step pre.result{{max-height:80px;background:#f0fff4;border-color:#52c41a}}
.flow-step pre.raw-api{{background:#fff7e6;border:1px solid #fa8c16;max-height:200px;overflow:auto}}
.raw-api-box{{background:#fff7e6;border:2px solid #fa8c16;border-radius:8px;padding:16px;margin:16px 0}}
.flow-arrow{{text-align:center;color:#999;font-size:1.2rem;margin:-4px 0}} 
</style></head>
<body>
<h1>📝 摘要 Prompt 调试</h1>
<p class="hint">填写全文与标题后运行，可查看实际发送的 prompt 与摘要结果。</p>

<div class="card">
<h2>1. Prompt 模板（可修改）</h2>
<p class="hint">占位符：{{title}} 标题 | {{text}} 正文</p>
<textarea name="prompt_template" id="promptTemplate" style="min-height:220px">{default_template}</textarea>
<button type="button" id="resetBtn" class="secondary" style="margin-top:8px;background:#666">恢复默认</button>
</div>

<div class="card">
<h2>2. 从链接抓取全文</h2>
<div style="display:flex;gap:8px;margin-bottom:8px">
<input type="url" id="urlInput" placeholder="粘贴文章链接，点击抓取" style="flex:1">
<button type="button" id="fetchBtn">抓取</button>
</div>
<p class="hint" id="fetchHint"></p>
</div>

<form id="f">
<div class="card">
<h2>3. 全文（保留完整，最多 10000 字）</h2>
<textarea name="full_text" placeholder="粘贴文章正文 或 用上方链接抓取..."></textarea>
<p class="hint">全文长度：<span id="len">0</span> 字</p>
</div>

<div class="card">
<h2>4. 标题</h2>
<input type="text" name="title" placeholder="文章标题（可选，抓取时会自动填充）">
</div>

<div class="card">
<button type="submit" id="btn">运行摘要</button>
<div id="result"></div>
</div>
</form>
<script type="application/json" id="defaultPromptJson">{default_template_json}</script>
<script>
document.querySelector('textarea[name=full_text]').oninput=function(){{document.getElementById('len').textContent=this.value.length}};
document.getElementById('resetBtn').onclick=function(){{document.getElementById('promptTemplate').value=JSON.parse(document.getElementById('defaultPromptJson').textContent);}};

document.getElementById('fetchBtn').onclick=async function(){{
 const url=document.getElementById('urlInput').value.trim();
 const hint=document.getElementById('fetchHint');
 const ta=document.querySelector('textarea[name=full_text]');
 const titleInput=document.querySelector('input[name=title]');
 if(!url){{hint.textContent='请输入链接';return}}
 hint.textContent='抓取中...';
 try{{
  const r=await fetch('/debug/summary-prompt/fetch?url='+encodeURIComponent(url));
  const j=await r.json();
  if(j.ok){{
   ta.value=j.full_text||'';
   titleInput.value=j.title||'';
   document.getElementById('len').textContent=ta.value.length;
   hint.textContent='已抓取 '+j.full_text_len+' 字';
  }}else{{
   hint.textContent='抓取失败：'+j.error;
  }}
 }}catch(ex){{
  hint.textContent='请求失败：'+ex.message;
 }}
}};

document.getElementById('f').onsubmit=async function(e){{
 e.preventDefault();
 const btn=document.getElementById('btn');
 const out=document.getElementById('result');
 btn.disabled=true;
 out.innerHTML='<span>请求中...</span>';
 const fd=new FormData(e.target);
 const body={{full_text:fd.get('full_text')||'',title:fd.get('title')||'',prompt_template:document.getElementById('promptTemplate').value||''}};
 if(body.full_text.length<50){{out.innerHTML='<span class="err">全文至少 50 字</span>';btn.disabled=false;return}}
 try{{
  const r=await fetch('/debug/summary-prompt/run',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(body)}});
  const j=await r.json();
  const esc=s=>String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  if(j.ok){{
   let flow='';
   if(j.trace&&j.trace.length){{
    flow='<h3 style="margin:24px 0 12px;font-size:1rem">📊 处理流程</h3><div class="flow">';
    j.trace.forEach((t,i)=>{{
     flow+='<div class="flow-step"><h4>'+esc(t.name)+'</h4><div class="desc">'+esc(t.desc)+'</div>';
     if(t.warn)flow+='<p style="color:#d46b08;font-weight:600;margin:8px 0">'+esc(t.warn)+'</p>';
     if(t.input)flow+='<pre>'+esc(JSON.stringify(t.input,null,2))+'</pre>';
     if(t.output_raw)flow+='<div class="hint" style="margin-top:12px;font-weight:600;color:#d46b08">⬇ API 原始返回（未做任何后处理）：</div><pre class="raw-api">'+esc(t.output_raw)+'</pre>';
     if(t.api_debug){{
      flow+='<div class="hint" style="margin-top:8px">调试信息：</div><pre>'+esc(JSON.stringify(t.api_debug,null,2))+'</pre>';
      if(t.api_debug.raw_response)flow+='<details style="margin-top:8px"><summary style="cursor:pointer;color:#d46b08;font-weight:600">📡 原始 HTTP 返回（完整 JSON）</summary><pre style="max-height:300px;overflow:auto;background:#fff7e6;padding:12px">'+esc(JSON.stringify(t.api_debug.raw_response,null,2))+'</pre></details>';
     }}
     if(t.input_raw)flow+='<div class="hint">输入（上一步 API 原始返回）：</div><pre>'+esc(t.input_raw)+'</pre>';
     if(t.output_preview)flow+='<pre>'+esc(t.output_preview)+'</pre>';
     if(t.output)flow+='<pre class="'+(t.step===4?'result':'')+'">'+esc(t.output)+'</pre>';
     flow+='</div>';
     if(i<j.trace.length-1)flow+='<div class="flow-arrow">↓</div>';
    }});
    flow+='</div>';
   }}
   const rawText=j.raw_api_response===undefined?'':(j.raw_api_response||'');
   const rawBox='<div class="raw-api-box"><h4 style="margin:0 0 8px;color:#d46b08">📡 API 原始返回</h4><pre style="margin:0;white-space:pre-wrap;max-height:200px;overflow:auto">'+esc(rawText||'[空]')+'</pre>'+(j.used_fallback?'<p class="hint" style="color:#d46b08;margin:8px 0 0">⚠️ API 返回为空，摘要来自兜底（原文前 180 字）</p>':'')+'</div>';
   out.innerHTML='<div class="ok"><h3 style="margin:0 0 12px;font-size:1rem">📄 摘要结果</h3><pre>'+esc(j.summary)+'</pre>'+rawBox+flow+'<details style="margin-top:16px"><summary>完整 Prompt</summary><pre>'+esc(j.prompt_sent)+'</pre></details><p class="hint">全文长度：'+j.full_text_len+' 字</p></div>';
  }}else{{
   out.innerHTML='<span class="err">'+esc(j.error||'')+'</span>';
  }}
 }}catch(ex){{
  out.innerHTML='<span class="err">'+esc(ex.message||'')+'</span>';
 }}
 btn.disabled=false;
}};
</script>
</body></html>'''


@app.get("/debug/summary-prompt", response_class=HTMLResponse)
def debug_summary_prompt():
    """摘要 Prompt 调试页：保留全文、保留初始 prompt"""
    return _summary_prompt_debug_page()


@app.get("/debug/summary-prompt/fetch")
def debug_summary_prompt_fetch(url: str = ""):
    """从链接抓取全文与标题，供调试页填充"""
    if not url or not url.startswith(("http://", "https://")):
        return JSONResponse({"ok": False, "error": "请提供有效链接"})
    try:
        from app.services.link_parser import extract_metadata
        meta = extract_metadata(url)
        if meta.get("error"):
            return JSONResponse({"ok": False, "error": meta["error"]})
        full_text = (meta.get("full_text") or "").strip()[:10000]
        title = meta.get("title", "")
        if len(full_text) < 50:
            return JSONResponse({"ok": False, "error": "抓取到的正文不足 50 字，可能需手动补充"})
        return JSONResponse({
            "ok": True,
            "full_text": full_text,
            "title": title,
            "full_text_len": len(full_text),
        })
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


@app.post("/debug/summary-prompt/run")
async def debug_summary_prompt_run(request: Request):
    """运行摘要并返回调试信息。与 ReadAfter 机器人同一套实现（generate_summary_unified）"""
    from app.services.summary_service import INITIAL_PROMPT_TEMPLATE, generate_summary_unified

    try:
        body = await request.json()
    except Exception:
        body = {}
    full_text = (body.get("full_text") or "").strip()
    title = (body.get("title") or "").strip()
    prompt_template = (body.get("prompt_template") or "").strip() or INITIAL_PROMPT_TEMPLATE

    if len(full_text) < 50:
        return JSONResponse({"ok": False, "error": "全文至少 50 字"})

    trace = []
    try:
        result = generate_summary_unified(
            full_text, title,
            prompt_template=prompt_template,
            trace=trace,
            return_debug=True,
        )
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})

    if isinstance(result, tuple):
        summary, debug = result
        return JSONResponse({
            "ok": True,
            "summary": summary,
            "raw_api_response": debug["raw_api_response"],
            "used_fallback": debug["used_fallback"],
            "prompt_sent": debug["prompt_sent"],
            "full_text_len": len(full_text[:10000]),
            "prompt_preview": (debug["prompt_sent"][:400] + "...") if len(debug["prompt_sent"]) > 400 else debug["prompt_sent"],
            "trace": debug["trace"],
        })
    return JSONResponse({"ok": True, "summary": result, "trace": trace})


@app.post("/webhook/feishu")
async def feishu_webhook(request: Request, background_tasks: BackgroundTasks):
    """飞书事件回调：立即返回 200，后台异步处理"""
    import time
    body = await request.json()
    _webhook_last.clear()
    _webhook_last["time"] = time.strftime("%Y-%m-%d %H:%M:%S")
    _webhook_last["type"] = body.get("type", "")
    _webhook_last["schema"] = body.get("schema", "")
    _webhook_last["body_keys"] = list(body.keys())
    ev = body.get("event", {})
    _webhook_last["event_type"] = ev.get("type", "") or body.get("header", {}).get("event_type", "")
    _webhook_last["msg_type"] = ev.get("message", {}).get("message_type", "")
    _webhook_last["chat_id_preview"] = (ev.get("message", {}).get("chat_id", ""))[:20] + "..."
    _webhook_last["chat_type"] = ev.get("message", {}).get("chat_type", "")

    parsed = parse_event(body)
    _webhook_last["parsed_type"] = parsed.get("type") if parsed else None

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
    if msg_type == "text" and str(content).strip().lower() in ("ping", "测试", "pong"):
        try:
            from app.feishu.bot import reply_message
            reply_message(
                chat_id, msg_id, "ReadAfter2.0 已收到～ ✅",
                chat_type=parsed.get("chat_type", ""),
                open_id=parsed.get("open_id", ""),
            )
        except Exception as e:
            _webhook_last["reply_error"] = str(e)
        return JSONResponse(content={})

    extra_parsed = parsed.get("extra", "")
    ct = parsed.get("chat_type", "")
    oid = parsed.get("open_id", "")

    # 立即回复，表示已收到正在处理（同一消息去重，避免飞书重试导致重复回复）
    import time as _time
    _now = _time.time()
    _window = 120  # 2 分钟内同一 message_id 不重复发「已收到」
    _last = _ack_msg_ids.get(msg_id, 0)
    if _now - _last > _window:
        _ack_msg_ids[msg_id] = _now
        if len(_ack_msg_ids) > 1000:
            _ack_msg_ids.clear()
        try:
            from app.feishu.bot import reply_message
            if msg_type in ("url", "urls", "image"):
                reply_message(chat_id, msg_id, "已收到，正在解析中…", ct, oid)
            elif msg_type == "text":
                reply_message(chat_id, msg_id, "已收到，正在检索中…", ct, oid)
        except Exception:
            pass

    def _safe_task(fn, *args, **kwargs):
        import time
        _task_status.clear()
        _task_status["started_at"] = time.strftime("%H:%M:%S")
        _task_status["type"] = msg_type
        try:
            fn(*args, **kwargs)
            _task_status["done_at"] = time.strftime("%H:%M:%S")
            _task_status["status"] = "ok"
        except Exception as e:
            _task_status["done_at"] = time.strftime("%H:%M:%S")
            _task_status["status"] = "error"
            _task_status["error"] = str(e)[:300]
            _webhook_last["task_error"] = str(e)
            try:
                from app.feishu.bot import reply_message
                reply_message(chat_id, msg_id, f"处理失败：{str(e)[:200]}", ct, oid)
            except Exception:
                pass

    if msg_type == "url":
        background_tasks.add_task(_safe_task, handle_url, chat_id, msg_id, content, extra_parsed, ct, oid)
    elif msg_type == "urls":
        for url in content:
            background_tasks.add_task(_safe_task, handle_url, chat_id, msg_id, url, extra_parsed, ct, oid)
    elif msg_type == "image":
        background_tasks.add_task(_safe_task, handle_image, chat_id, msg_id, content, ct, oid)
    elif msg_type == "text":
        background_tasks.add_task(_safe_task, handle_question, chat_id, msg_id, content, ct, oid)

    return JSONResponse(content={})


if __name__ == "__main__":
    port = int(os.getenv("PORT", str(PORT)))
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=port)
