"""LLM + embedding provider abstraction, configured through .env.

Providers:
* ``azure``     - Azure OpenAI chat deployment (vision-capable, e.g. gpt-4o)
* ``anthropic`` - Anthropic Claude models (vision-capable)
* ``mock``      - deterministic offline parsers for demos/tests without keys

Structured output uses LangChain ``with_structured_output`` (tool calling) so
responses are validated against the Pydantic schema.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import math
import os
import re
from abc import ABC, abstractmethod
from functools import lru_cache
from typing import TypeVar

from pydantic import BaseModel

from cori.config import get_settings

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)


class SchemaValidationError(RuntimeError):
    """The model answered but the answer did not validate against the schema."""

    def __init__(self, message: str, raw_text: str | None = None):
        super().__init__(message)
        self.raw_text = raw_text


class ProviderUnavailable(RuntimeError):
    """Provider is not configured (missing key / endpoint)."""


class LLMProvider(ABC):
    provider_name: str
    model_name: str
    supports_vision: bool = True

    @abstractmethod
    def extract_structured(self, system: str, user_text: str, schema: type[T],
                           images: list[bytes] | None = None, source_path: str | None = None) -> tuple[T, str]:
        """Return (validated object, raw model output text)."""

    @abstractmethod
    def complete(self, system: str, user: str) -> str:
        """Plain text completion."""


# ----------------------------------------------------------------------------
# LangChain-backed providers
# ----------------------------------------------------------------------------
class LangChainProvider(LLMProvider):
    def __init__(self, provider_name: str, model_name: str):
        self.provider_name = provider_name
        self.model_name = model_name
        self._chat = self._build_chat()

    def _build_chat(self):
        temp = os.getenv("LLM_TEMPERATURE", "").strip()
        kwargs = {"temperature": float(temp)} if temp else {}
        if self.provider_name == "azure":
            from langchain_openai import AzureChatOpenAI
            if not os.getenv("AZURE_OPENAI_API_KEY") or not os.getenv("AZURE_OPENAI_ENDPOINT"):
                raise ProviderUnavailable("AZURE_OPENAI_API_KEY / AZURE_OPENAI_ENDPOINT not set")
            return AzureChatOpenAI(azure_deployment=self.model_name,
                                   api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21"),
                                   timeout=120, max_retries=2, **kwargs)
        if self.provider_name == "anthropic":
            from langchain_anthropic import ChatAnthropic
            if not os.getenv("ANTHROPIC_API_KEY"):
                raise ProviderUnavailable("ANTHROPIC_API_KEY not set")
            return ChatAnthropic(model=self.model_name, max_tokens=8000, timeout=120, max_retries=2, **kwargs)
        raise ProviderUnavailable(f"unknown provider {self.provider_name!r}")

    def extract_structured(self, system, user_text, schema, images=None, source_path=None):
        from langchain_core.messages import HumanMessage, SystemMessage

        method = "function_calling" if self.provider_name == "azure" else None
        kwargs = {"include_raw": True}
        if method:
            kwargs["method"] = method
        llm = self._chat.with_structured_output(schema, **kwargs)
        content: list[dict] = [{"type": "text", "text": user_text}]
        for img in images or []:
            b64 = base64.b64encode(img).decode()
            content.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}})
        out = llm.invoke([SystemMessage(content=system), HumanMessage(content=content)])
        raw_msg = out.get("raw")
        raw_text = _raw_to_text(raw_msg)
        if out.get("parsing_error") is not None or out.get("parsed") is None:
            raise SchemaValidationError(f"schema validation failed: {out.get('parsing_error')}", raw_text)
        parsed = out["parsed"]
        if not isinstance(parsed, schema):
            parsed = schema.model_validate(parsed)
        return parsed, raw_text

    def complete(self, system, user):
        from langchain_core.messages import HumanMessage, SystemMessage
        msg = self._chat.invoke([SystemMessage(content=system), HumanMessage(content=user)])
        return _raw_to_text(msg)


def _raw_to_text(msg) -> str:
    if msg is None:
        return ""
    tool_calls = getattr(msg, "tool_calls", None)
    if tool_calls:
        import json
        return json.dumps([tc.get("args") for tc in tool_calls], default=str)
    content = getattr(msg, "content", msg)
    if isinstance(content, list):
        return "".join(c.get("text", "") if isinstance(c, dict) else str(c) for c in content)
    return str(content)


def default_model(provider: str) -> str:
    if provider == "azure":
        return os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o")
    if provider == "anthropic":
        return os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5-5")
    return "mock-deterministic-v1"


def get_provider(provider: str = "default", model: str = "default", strategy: str = "B") -> LLMProvider:
    """Build a provider. ``LLM_PROVIDER=mock`` forces the offline mock for every
    agent so the whole pipeline runs without network access."""
    global_provider = get_settings().llm_provider
    name = global_provider if provider in ("default", "", None) else provider.lower()
    if global_provider == "mock":
        name = "mock"
    if name == "mock":
        from cori.llm.mock import MockProvider
        return MockProvider(strategy=strategy)
    model_name = default_model(name) if model in ("default", "", None) else model
    return _cached_langchain(name, model_name)


@lru_cache
def _cached_langchain(name: str, model_name: str) -> LangChainProvider:
    return LangChainProvider(name, model_name)


# ----------------------------------------------------------------------------
# Embeddings
# ----------------------------------------------------------------------------
class Embedder(ABC):
    model_name: str

    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]: ...


_TOKEN = re.compile(r"[a-z0-9]+")


class LocalHashEmbedder(Embedder):
    """Deterministic hashed bag-of-words (+ bigrams) embedding. Offline,
    dependency-free, good enough for "similar line items" in the demo."""

    def __init__(self, dim: int = 384):
        self.dim = dim
        self.model_name = f"local-hash-bow-{dim}"

    def _vec(self, text: str) -> list[float]:
        v = [0.0] * self.dim
        toks = _TOKEN.findall(text.lower())
        feats = toks + [f"{a}_{b}" for a, b in zip(toks, toks[1:])]
        for f in feats:
            h = hashlib.md5(f.encode()).digest()
            idx = int.from_bytes(h[:4], "little") % self.dim
            v[idx] += 1.0 if h[4] % 2 == 0 else -1.0
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norm for x in v]

    def embed(self, texts):
        return [self._vec(t) for t in texts]


class AzureEmbedder(Embedder):
    def __init__(self, dim: int):
        from langchain_openai import AzureOpenAIEmbeddings
        deployment = os.getenv("AZURE_OPENAI_EMBEDDING_DEPLOYMENT", "text-embedding-3-small")
        self.model_name = f"azure:{deployment}:{dim}"
        self._emb = AzureOpenAIEmbeddings(azure_deployment=deployment, dimensions=dim,
                                          api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21"))

    def embed(self, texts):
        return self._emb.embed_documents(texts)


@lru_cache
def get_embedder() -> Embedder:
    cfg = get_settings().embeddings
    if cfg.provider == "azure" and get_settings().llm_provider != "mock":
        return AzureEmbedder(cfg.dim)
    return LocalHashEmbedder(cfg.dim)
