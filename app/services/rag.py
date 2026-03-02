"""RAG 检索与问答（基于飞书表格 + ChromaDB）"""
from app.feishu.bitable import list_records
from app.services.ai_service import answer_with_context

# 内存向量库（重启后从飞书重建）
_chroma_client = None
_chroma_collection = None


def _get_collection():
    """懒加载 ChromaDB 集合"""
    global _chroma_client, _chroma_collection
    if _chroma_collection is not None:
        return _chroma_collection

    try:
        import chromadb
        from chromadb.config import Settings

        _chroma_client = chromadb.Client(Settings(anonymized_telemetry=False))
        _chroma_collection = _chroma_client.get_or_create_collection(
            name="readafter",
            metadata={"hnsw:space": "cosine"},
        )
        _rebuild_index()
    except Exception:
        _chroma_collection = None
    return _chroma_collection


def _rebuild_index():
    """从飞书表格重建向量索引"""
    global _chroma_client, _chroma_collection
    if _chroma_collection is None:
        return
    try:
        _chroma_client.delete_collection("readafter")
        _chroma_collection = _chroma_client.get_or_create_collection(
            name="readafter", metadata={"hnsw:space": "cosine"}
        )
        records = list_records(limit=200)
        if not records:
            return
        ids, documents = [], []
        for r in records:
            rid = r.get("record_id", "")
            fields = r.get("fields", {})
            full = fields.get("全文", "") or fields.get("摘要", "") or ""
            title = fields.get("内容", "") or ""
            if full or title:
                ids.append(rid)
                documents.append(f"{title}\n{full}"[:8000])
        if ids and documents:
            from app.services.ai_service import get_client

            client = get_client()
            resp = client.embeddings.create(model="text-embedding-3-small", input=documents)
            embeds = [e.embedding for e in resp.data]
            _chroma_collection.add(ids=ids, documents=documents, embeddings=embeds)
    except Exception:
        pass


def add_to_index(record_id: str, title: str, full_text: str):
    """新增一条记录到向量索引"""
    coll = _get_collection()
    if coll is None:
        return
    try:
        text = f"{title}\n{full_text}"[:8000]
        if not text:
            return
        client = __import__("app.services.ai_service", fromlist=["get_client"]).get_client()
        resp = client.embeddings.create(model="text-embedding-3-small", input=[text])
        emb = resp.data[0].embedding
        coll.add(ids=[record_id], documents=[text], embeddings=[emb])
    except Exception:
        pass


def search_and_answer(question: str, top_k: int = 5) -> str:
    """
    检索相关记录并生成回答。
    若 ChromaDB 不可用，则回退为「取最近 N 条 + LLM 回答」。
    """
    try:
        records = list_records(limit=50)
        if not records:
            return "暂无已收集的内容，请先发送链接或图片让我帮你记录。"

        coll = _get_collection()
        if coll is not None:
            try:
                client = __import__("app.services.ai_service", fromlist=["get_client"]).get_client()
                q_emb = client.embeddings.create(
                    model="text-embedding-3-small",
                    input=[question],
                )
                results = coll.query(
                    query_embeddings=[q_emb.data[0].embedding],
                    n_results=min(top_k, len(records)),
                )
                if results and results.get("ids") and results["ids"][0]:
                    rid_set = set(results["ids"][0])
                    ctx_parts = []
                    for r in records:
                        if r.get("record_id") in rid_set:
                            f = r.get("fields", {})
                            ctx_parts.append(
                                f"【{f.get('内容','')}】\n{f.get('全文','') or f.get('摘要','')}\n"
                            )
                    context = "\n---\n".join(ctx_parts)
                    return answer_with_context(question, context)
            except Exception:
                pass

        # 回退：取最近几条
        ctx_parts = []
        for r in records[:top_k]:
            f = r.get("fields", {})
            ctx_parts.append(f"【{f.get('内容','')}】\n{f.get('全文','') or f.get('摘要','')}\n")
        context = "\n---\n".join(ctx_parts)
        return answer_with_context(question, context)
    except Exception as e:
        return f"检索出错: {str(e)}"
