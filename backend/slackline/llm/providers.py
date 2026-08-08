"""The only module that talks to model APIs. Gemini Flash primary, Groq
automatic fallback, one provider interface, token accounting on every call.

Interface consumed by selector/narrator/pipeline:
* ``complete_json(prompt) -> JsonResult`` — structured output, payload parsed
* ``stream_text(prompt) -> Iterator[str]`` — prose deltas
* ``last_usage`` — Usage of the most recent call (streaming included)
* ``fallback_engaged`` — True once Groq had to take over, for telemetry

Both providers are called over plain REST with httpx: no vendor SDKs, so the
container stays slim and the cold start short. Failures raise; callers
(selector/narrator) degrade to the deterministic paths.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Iterator, Optional

import httpx

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
DEFAULT_GROQ_MODEL = "llama-3.3-70b-versatile"

TIMEOUT_S = 20.0


def _env(name: str) -> str:
    """Env values sometimes arrive with pasted wrapping quotes; strip them.
    Never log or echo the value."""
    return os.environ.get(name, "").strip().strip("'\"")


@dataclass(frozen=True)
class Usage:
    tokens_in: int
    tokens_out: int


@dataclass(frozen=True)
class JsonResult:
    payload: Any
    model: str
    provider: str
    tokens_in: int
    tokens_out: int


class ModelProvider:
    def __init__(
        self,
        gemini_key: str = "",
        groq_key: str = "",
        gemini_model: str = DEFAULT_GEMINI_MODEL,
        groq_model: str = DEFAULT_GROQ_MODEL,
        timeout_s: float = TIMEOUT_S,
    ):
        self._gemini_key = gemini_key
        self._groq_key = groq_key
        self._gemini_model = gemini_model
        self._groq_model = groq_model
        self._timeout = timeout_s
        self.last_usage: Optional[Usage] = None
        self.fallback_engaged = False

    # -- public interface ---------------------------------------------------

    def complete_json(self, prompt: str) -> JsonResult:
        """Structured output. Gemini first, Groq on any failure. Raises only
        when every configured provider failed."""
        errors: list[str] = []
        if self._gemini_key:
            try:
                return self._gemini_json(prompt)
            except Exception as exc:
                errors.append(f"gemini: {type(exc).__name__}")
        if self._groq_key:
            try:
                if self._gemini_key:
                    self.fallback_engaged = True
                return self._groq_json(prompt)
            except Exception as exc:
                errors.append(f"groq: {type(exc).__name__}")
        raise RuntimeError("no model provider succeeded: " + "; ".join(errors))

    def stream_text(self, prompt: str) -> Iterator[str]:
        """Prose deltas. Gemini streaming first, Groq streaming on failure.
        ``last_usage`` is set once the stream finishes."""
        if self._gemini_key:
            try:
                yield from self._gemini_stream(prompt)
                return
            except Exception:
                pass
        if self._groq_key:
            if self._gemini_key:
                self.fallback_engaged = True
            yield from self._groq_stream(prompt)
            return
        raise RuntimeError("no model provider configured")

    # -- Gemini ---------------------------------------------------------------

    def _gemini_json(self, prompt: str) -> JsonResult:
        url = f"{GEMINI_URL}/{self._gemini_model}:generateContent"
        body = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                # Ranking with one-line reasons needs no deliberation; the
                # default thinking budget costs 10+ seconds on 2.5 models.
                "thinkingConfig": {"thinkingBudget": 0},
            },
        }
        resp = httpx.post(
            url,
            headers={"x-goog-api-key": self._gemini_key},
            json=body,
            timeout=self._timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        usage = data.get("usageMetadata", {})
        tokens_in = int(usage.get("promptTokenCount", 0))
        tokens_out = int(usage.get("candidatesTokenCount", 0))
        self.last_usage = Usage(tokens_in, tokens_out)
        return JsonResult(
            payload=_coerce_ranking_payload(json.loads(text)),
            model=self._gemini_model,
            provider="gemini",
            tokens_in=tokens_in,
            tokens_out=tokens_out,
        )

    def _gemini_stream(self, prompt: str) -> Iterator[str]:
        url = f"{GEMINI_URL}/{self._gemini_model}:streamGenerateContent?alt=sse"
        body = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"thinkingConfig": {"thinkingBudget": 0}},
        }
        tokens_in = tokens_out = 0
        with httpx.stream(
            "POST",
            url,
            headers={"x-goog-api-key": self._gemini_key},
            json=body,
            timeout=self._timeout,
        ) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line.startswith("data:"):
                    continue
                chunk = json.loads(line[5:].strip())
                usage = chunk.get("usageMetadata")
                if usage:
                    tokens_in = int(usage.get("promptTokenCount", tokens_in))
                    tokens_out = int(usage.get("candidatesTokenCount", tokens_out))
                for cand in chunk.get("candidates", ()):
                    for part in cand.get("content", {}).get("parts", ()):
                        text = part.get("text")
                        if text:
                            yield text
        self.last_usage = Usage(tokens_in, tokens_out)

    # -- Groq (OpenAI-compatible) ------------------------------------------------

    def _groq_json(self, prompt: str) -> JsonResult:
        body = {
            "model": self._groq_model,
            "messages": [{"role": "user", "content": prompt}],
            "response_format": {"type": "json_object"},
        }
        resp = httpx.post(
            GROQ_URL,
            headers={"Authorization": f"Bearer {self._groq_key}"},
            json=body,
            timeout=self._timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        text = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})
        tokens_in = int(usage.get("prompt_tokens", 0))
        tokens_out = int(usage.get("completion_tokens", 0))
        self.last_usage = Usage(tokens_in, tokens_out)
        return JsonResult(
            payload=_coerce_ranking_payload(json.loads(text)),
            model=self._groq_model,
            provider="groq",
            tokens_in=tokens_in,
            tokens_out=tokens_out,
        )

    def _groq_stream(self, prompt: str) -> Iterator[str]:
        body = {
            "model": self._groq_model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        tokens_in = tokens_out = 0
        with httpx.stream(
            "POST",
            GROQ_URL,
            headers={"Authorization": f"Bearer {self._groq_key}"},
            json=body,
            timeout=self._timeout,
        ) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line.startswith("data:"):
                    continue
                raw = line[5:].strip()
                if raw == "[DONE]":
                    break
                chunk = json.loads(raw)
                usage = chunk.get("usage")
                if usage:
                    tokens_in = int(usage.get("prompt_tokens", tokens_in))
                    tokens_out = int(usage.get("completion_tokens", tokens_out))
                for choice in chunk.get("choices", ()):
                    text = choice.get("delta", {}).get("content")
                    if text:
                        yield text
        self.last_usage = Usage(tokens_in, tokens_out)


def _coerce_ranking_payload(parsed: Any) -> Any:
    """The Selector expects a list of objects. Gemini's JSON mime type can
    return a bare array; Groq's json_object mode wraps it in an object.
    Unwrap the first list value when needed."""
    if isinstance(parsed, list):
        return parsed
    if isinstance(parsed, dict):
        for value in parsed.values():
            if isinstance(value, list):
                return value
    return parsed


def from_env() -> Optional[ModelProvider]:
    """Provider from GEMINI_API_KEY / GROQ_API_KEY, or None when keyless —
    the caller then uses heuristic ranking and template narration."""
    gemini = _env("GEMINI_API_KEY")
    groq = _env("GROQ_API_KEY")
    if not gemini and not groq:
        return None
    return ModelProvider(
        gemini_key=gemini,
        groq_key=groq,
        gemini_model=_env("GEMINI_MODEL") or DEFAULT_GEMINI_MODEL,
        groq_model=_env("GROQ_MODEL") or DEFAULT_GROQ_MODEL,
    )
