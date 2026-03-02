"""AI Builder Space 调用封装"""
from openai import OpenAI
from app.config import AI_BUILDER_TOKEN, AI_BUILDER_BASE_URL


def get_client() -> OpenAI:
    return OpenAI(api_key=AI_BUILDER_TOKEN, base_url=AI_BUILDER_BASE_URL)


def chat(messages: list[dict], model: str = "deepseek", max_tokens: int = 1500) -> str:
    """简单对话"""
    if not AI_BUILDER_TOKEN:
        return "AI 服务未配置"
    client = get_client()
    resp = client.chat.completions.create(
        model=model,
        messages=messages,
        max_tokens=max_tokens,
    )
    return (resp.choices[0].message.content or "").strip()


def answer_with_context(question: str, context: str) -> str:
    """基于上下文回答问题（RAG 用）"""
    messages = [
        {
            "role": "system",
            "content": "你是一个 helpful 的助手。请严格基于以下【已收集内容】回答用户问题。"
            "如果内容中没有相关信息，请如实说明。不要编造内容。",
        },
        {"role": "user", "content": f"【已收集内容】\n{context}\n\n【用户问题】\n{question}"},
    ]
    return chat(messages)
