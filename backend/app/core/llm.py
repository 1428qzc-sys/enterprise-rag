"""LLM 提供方抽象（支持流式）。

- ``openai``：任意 OpenAI 兼容 /chat/completions（OpenAI、DeepSeek……）
- ``ollama``：本地 Ollama /api/chat
- ``echo``：回显式假模型，供测试与离线演示（不产生真实推理）
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import AsyncIterator, Dict, List, Optional

import httpx

from ..config import settings

Message = Dict[str, str]  # {"role": "...", "content": "..."}


class BaseLLM(ABC):
    def __init__(self, model: str, temperature: float, max_tokens: int):
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens

    @abstractmethod
    async def astream(self, messages: List[Message]) -> AsyncIterator[str]:
        """逐 token 产出增量文本。"""
        ...
        yield ""  # pragma: no cover

    async def acomplete(self, messages: List[Message]) -> str:
        parts: List[str] = []
        async for delta in self.astream(messages):
            parts.append(delta)
        return "".join(parts)


class OpenAILLM(BaseLLM):
    def __init__(self, model, temperature, max_tokens, base_url: str, api_key: str):
        super().__init__(model, temperature, max_tokens)
        from openai import AsyncOpenAI

        self._client = AsyncOpenAI(base_url=base_url, api_key=api_key or "not-needed")

    async def astream(self, messages: List[Message]) -> AsyncIterator[str]:
        stream = await self._client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            stream=True,
        )
        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta


class OllamaLLM(BaseLLM):
    def __init__(self, model, temperature, max_tokens, base_url: str):
        super().__init__(model, temperature, max_tokens)
        self._base = base_url.rstrip("/")

    async def astream(self, messages: List[Message]) -> AsyncIterator[str]:
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "options": {"temperature": self.temperature, "num_predict": self.max_tokens},
        }
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream("POST", f"{self._base}/api/chat", json=payload) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    data = json.loads(line)
                    token = data.get("message", {}).get("content")
                    if token:
                        yield token
                    if data.get("done"):
                        break


class EchoLLM(BaseLLM):
    """把上下文与问题拼成一段可预测的中文回答，逐字产出。仅测试/演示用。"""

    async def astream(self, messages: List[Message]) -> AsyncIterator[str]:
        question = ""
        for m in reversed(messages):
            if m["role"] == "user":
                question = m["content"]
                break
        answer = (
            f"[echo] 已根据检索到的上下文回答问题：{question[:80]} "
            f"参考来源见下方引用 [1]。"
        )
        for ch in answer:
            yield ch


def make_llm(provider: Optional[str] = None, model: Optional[str] = None) -> BaseLLM:
    provider = provider or settings.llm_provider
    model = model or settings.llm_model
    if provider == "openai":
        return OpenAILLM(
            model=model,
            temperature=settings.llm_temperature,
            max_tokens=settings.llm_max_tokens,
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
        )
    if provider == "ollama":
        return OllamaLLM(
            model=model,
            temperature=settings.llm_temperature,
            max_tokens=settings.llm_max_tokens,
            base_url=settings.ollama_base_url,
        )
    if provider == "echo":
        return EchoLLM(model=model, temperature=settings.llm_temperature, max_tokens=settings.llm_max_tokens)
    raise ValueError(f"未知 llm provider: {provider}")
