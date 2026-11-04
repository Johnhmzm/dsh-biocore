from __future__ import annotations

import json
import os
import urllib.request
from typing import Callable, Dict, Optional

from .llm_adapter import build_prompt
from .types import LLMRequest


class LLMConfigError(RuntimeError):
    pass


class LLMClientError(RuntimeError):
    pass


def _http_post(endpoint: str, headers: Dict[str, str], payload: Dict[str, object], timeout: float) -> Dict[str, object]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(endpoint, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:800]
        raise LLMClientError(f"HTTP {e.code}: {body}") from e


class OpenAICompatClient:
    """Minimal OpenAI-compatible /chat/completions client.

    No state, no memory, no decision-making: text in, text out.
    """

    def __init__(
        self,
        model: str,
        base_url: Optional[str] = None,
        endpoint: Optional[str] = None,
        api_key: str = "",
        timeout: float = 30.0,
        temperature: float = 0.7,
        chat_template_kwargs: Optional[Dict[str, object]] = None,
        character_prompt: Optional[str] = None,
        transport: Optional[Callable[[Dict[str, object]], Dict[str, object]]] = None,
    ):
        if endpoint:
            self.endpoint = endpoint
        elif base_url:
            self.endpoint = base_url.rstrip("/") + "/chat/completions"
        else:
            raise LLMConfigError("need base_url or endpoint")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        self.temperature = temperature
        self.chat_template_kwargs = chat_template_kwargs
        self.character_prompt = character_prompt
        self.transport = transport
        self.last_usage = {}
        self.last_finish_reason = ""

    @classmethod
    def from_env(cls, **overrides) -> "OpenAICompatClient":
        base_url = overrides.pop("base_url", None) or os.getenv("LLM_BASE_URL") or os.getenv("OPENAI_BASE_URL")
        api_key = overrides.pop("api_key", "") or os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY") or ""
        model = overrides.pop("model", None) or os.getenv("LLM_MODEL") or os.getenv("OPENAI_MODEL")
        character_prompt = overrides.pop("character_prompt", None)
        if character_prompt is None:
            path = os.getenv("LLM_CHARACTER_PROMPT_FILE")
            if path and os.path.isfile(path):
                character_prompt = open(path, encoding="utf-8").read()
        kwargs = overrides.pop("chat_template_kwargs", None)
        if kwargs is None:
            raw = os.getenv("LLM_CHAT_TEMPLATE_KWARGS")
            if raw:
                kwargs = json.loads(raw)
        if not model:
            raise LLMConfigError("missing LLM_MODEL (or OPENAI_MODEL)")
        return cls(
            model=model,
            base_url=base_url or "http://127.0.0.1:11434/v1",
            api_key=api_key,
            chat_template_kwargs=kwargs,
            character_prompt=character_prompt,
            **overrides,
        )

    def _messages(self, request: LLMRequest):
        prompt = build_prompt(request, self.character_prompt)
        return [
            {"role": "system", "content": str(prompt["system"])},
            {"role": "user", "content": str(prompt["user"])},
        ]

    def _headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def generate(self, request: LLMRequest) -> str:
        payload: Dict[str, object] = {
            "model": self.model,
            "messages": self._messages(request),
            "temperature": self.temperature,
        }
        try:
            if int(request.max_tokens or 0) > 0:
                payload["max_tokens"] = int(request.max_tokens)
        except Exception:
            payload["max_tokens"] = int(request.max_tokens)
        if self.chat_template_kwargs:
            payload["chat_template_kwargs"] = self.chat_template_kwargs
        if getattr(request, "response_format", None):
            payload["response_format"] = request.response_format
        try:
            if self.transport is not None:
                data = self.transport(payload)
            else:
                data = _http_post(self.endpoint, self._headers(), payload, self.timeout)
            self.last_usage = dict(data.get("usage") or {})
            self.last_finish_reason = str(data["choices"][0].get("finish_reason") or "")
            return str(data["choices"][0]["message"]["content"])
        except LLMClientError:
            raise
        except Exception as exc:  # network / schema errors
            raise LLMClientError(f"LLM call failed: {exc}") from exc
