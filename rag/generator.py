"""LLM 生成：把检索到的上下文与用户问题拼成提示词并生成回答。"""
from __future__ import annotations

from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate

from config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL, LLM_TEMPERATURE

_SYSTEM = """你是一个忠实于给定资料的问答助手。请只依据下面的资料回答用户问题。

要求：
1. 若资料不足，直接说明"资料中没有相关信息"，不要编造。
2. 回答末尾用 [来源] 标注依据的文件名。

资料：
{context}"""


def build_llm() -> ChatOpenAI:
    return ChatOpenAI(
        model=LLM_MODEL,
        api_key=LLM_API_KEY,
        base_url=LLM_BASE_URL,
        temperature=LLM_TEMPERATURE,
    )


def answer(llm: ChatOpenAI, question: str, hits: list[dict]) -> str:
    """用检索片段构造上下文并调用 LLM 作答。"""
    if not hits:
        return "未检索到相关信息，请补充知识库或换个问法。"

    context = "\n\n---\n\n".join(f"[{h['source']}]\n{h['content']}" for h in hits)
    prompt = ChatPromptTemplate.from_messages(
        [("system", _SYSTEM), ("human", "问题：{question}")]
    )
    chain = prompt | llm
    return chain.invoke({"question": question, "context": context}).content