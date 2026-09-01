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


class RateLimited(LLMError):
    """無料枠の上限。待てば必ず通るので、失敗回数には数えない。"""

    def __init__(self, message: str, retry_after: float):
        super().__init__(message)
        self.retry_after = retry_after


_RETRY_AFTER = re.compile(r"retry in ([\d.]+)s|\"retryDelay\":\s*\"([\d.]+)s\"")


def _retry_after(body: str) -> float:
    """Gemini が返す待ち時間を読み取る。読めなければ1分待つ。"""
    m = _RETRY_AFTER.search(body)
    if m:
        return float(m.group(1) or m.group(2)) + 2
    return 60.0


ROLES = ("outline", "chapter", "assemble")


class LLM:
    def __init__(self, cfg: dict):
        self.provider = os.environ.get("LLM_PROVIDER") or cfg["provider"]
        self.model = os.environ.get("LLM_MODEL") or (
            cfg["gemini_model"] if self.provider == "gemini" else cfg["openrouter_model"]
        )
        # モデル名を明示指定されたら、工程別の設定より優先して全工程に使う
        if os.environ.get("LLM_MODEL"):
            self.role_models = {role: self.model for role in ROLES}
        else:
            configured = cfg.get("models") or {}
            self.role_models = {
                role: os.environ.get(f"LLM_MODEL_{role.upper()}")
                or configured.get(role)
                or self.model
                for role in ROLES
            }
        self.temperature = float(cfg.get("temperature", 0.9))
        self.max_tokens = int(cfg.get("max_output_tokens", 8192))
        self.retries = int(cfg.get("retries", 3))
        self.rate_limit_retries = int(cfg.get("rate_limit_retries", 6))

    def generate(
        self, system: str, user: str, json_mode: bool = False, role: str = "chapter"
    ) -> str:
        model = self.role_models.get(role, self.model)
        last: Exception | None = None
        failures, waits = 0, 0
        while failures < self.retries:
            try:
                if self.provider == "gemini":
                    return self._gemini(system, user, json_mode, model)
                return self._openrouter(system, user, json_mode, model)
            except RateLimited as exc:
                last = exc
                if waits >= self.rate_limit_retries:
                    break
                waits += 1
                print(f"      レート制限に当たりました。{exc.retry_after:.0f}秒待って再試行します")
                time.sleep(exc.retry_after)
            except Exception as exc:  # noqa: BLE001
                last = exc
                failures += 1
                if failures < self.retries:
                    time.sleep(4 * failures)
        raise LLMError(f"{self.provider}/{model} の呼び出しに失敗しました: {last}")

    def generate_json(self, system: str, user: str, role: str = "chapter") -> dict:
        last: Exception | None = None
        for _ in range(self.retries):
            raw = self.generate(system, user, json_mode=True, role=role)
            try:
                return _parse_json(raw)
            except Exception as exc:  # noqa: BLE001
                last = exc
        raise LLMError(f"JSONを取得できませんでした: {last}")

    def _gemini(self, system: str, user: str, json_mode: bool, model: str) -> str:
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
            GEMINI_ENDPOINT.format(model=model),
            params={"key": key},
            json=body,
            timeout=300,
        )
        if res.status_code == 429:
            # どの枠を使い切ったのか（分あたりか日あたりか）が message 末尾に出るので長めに残す
            raise RateLimited(f"Gemini 429: {res.text[:900]}", _retry_after(res.text))
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

    def _openrouter(self, system: str, user: str, json_mode: bool, model: str) -> str:
        key = os.environ.get("OPENROUTER_API_KEY")
        if not key:
            raise LLMError("OPENROUTER_API_KEY が設定されていません")
        body = {
            "model": model,
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
        if res.status_code == 429:
            wait = float(res.headers.get("Retry-After") or 30) + 2
            raise RateLimited(f"OpenRouter 429: {res.text[:300]}", wait)
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
