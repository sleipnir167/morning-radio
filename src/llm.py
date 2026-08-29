"""Gemini / OpenRouter を同一インターフェースで叩く薄いクライアント。"""
from __future__ import annotations

import json
import os
import re
import time

import requests

GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
OPENROUTER_ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"


class LLMError(RuntimeError):
    pass


class LLM:
    def __init__(self, cfg: dict):
        self.provider = os.environ.get("LLM_PROVIDER") or cfg["provider"]
        self.model = os.environ.get("LLM_MODEL") or (
            cfg["gemini_model"] if self.provider == "gemini" else cfg["openrouter_model"]
        )
        self.temperature = float(cfg.get("temperature", 0.9))
        self.max_tokens = int(cfg.get("max_output_tokens", 8192))
        self.retries = int(cfg.get("retries", 3))

    def generate(self, system: str, user: str, json_mode: bool = False) -> str:
        last: Exception | None = None
        for attempt in range(self.retries):
            try:
                if self.provider == "gemini":
                    return self._gemini(system, user, json_mode)
                return self._openrouter(system, user, json_mode)
            except Exception as exc:  # noqa: BLE001
                last = exc
                if attempt < self.retries - 1:
                    time.sleep(4 * (attempt + 1))
        raise LLMError(f"{self.provider}/{self.model} の呼び出しに失敗しました: {last}")

    def generate_json(self, system: str, user: str) -> dict:
        last: Exception | None = None
        for _ in range(self.retries):
            raw = self.generate(system, user, json_mode=True)
            try:
                return _parse_json(raw)
            except Exception as exc:  # noqa: BLE001
                last = exc
        raise LLMError(f"JSONを取得できませんでした: {last}")

    def _gemini(self, system: str, user: str, json_mode: bool) -> str:
        key = os.environ.get("GEMINI_API_KEY")
        if not key:
            raise LLMError("GEMINI_API_KEY が設定されていません")
        gen_cfg = {
            "temperature": self.temperature,
            "maxOutputTokens": self.max_tokens,
        }
        if json_mode:
            gen_cfg["responseMimeType"] = "application/json"
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": gen_cfg,
        }
        res = requests.post(
            GEMINI_ENDPOINT.format(model=self.model),
            params={"key": key},
            json=body,
            timeout=300,
        )
        if res.status_code != 200:
            raise LLMError(f"Gemini {res.status_code}: {res.text[:500]}")
        data = res.json()
        candidates = data.get("candidates") or []
        if not candidates:
            raise LLMError(f"Gemini が候補を返しませんでした: {json.dumps(data)[:500]}")
        parts = candidates[0].get("content", {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts).strip()
        if not text:
            raise LLMError(f"Gemini の応答が空です (finishReason={candidates[0].get('finishReason')})")
        return text

    def _openrouter(self, system: str, user: str, json_mode: bool) -> str:
        key = os.environ.get("OPENROUTER_API_KEY")
        if not key:
            raise LLMError("OPENROUTER_API_KEY が設定されていません")
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        res = requests.post(
            OPENROUTER_ENDPOINT,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json=body,
            timeout=300,
        )
        if res.status_code != 200:
            raise LLMError(f"OpenRouter {res.status_code}: {res.text[:500]}")
        data = res.json()
        text = (data["choices"][0]["message"]["content"] or "").strip()
        if not text:
            raise LLMError("OpenRouter の応答が空です")
        return text


_BAD_ESCAPE = re.compile(r'\\(?!["\\/bfnrtu])')


def _candidates(raw: str):
    yield raw
    fenced = re.search(r"```(?:json)?\s*(.+?)\s*```", raw, re.S)
    if fenced:
        yield fenced.group(1)
    start, end = raw.find("{"), raw.rfind("}")
    if start != -1 and end > start:
        yield raw[start : end + 1]


def _parse_json(raw: str) -> dict:
    for text in _candidates(raw):
        for attempt in (text, _BAD_ESCAPE.sub(r"\\\\", text)):
            try:
                return json.loads(attempt, strict=False)
            except json.JSONDecodeError:
                continue
    raise LLMError(f"JSONとして解釈できない応答でした: {raw[:300]}")
