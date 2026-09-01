"""過去回のトピック履歴。JSONL をリポジトリにコミットして永続化する。"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

HISTORY_PATH = Path("history/topics.jsonl")


def load(lookback_days: int) -> list[dict]:
    if not HISTORY_PATH.exists():
        return []
    cutoff = date.today() - timedelta(days=lookback_days)
    entries = []
    for line in HISTORY_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
            if datetime.strptime(entry["date"], "%Y-%m-%d").date() >= cutoff:
                entries.append(entry)
        except (json.JSONDecodeError, KeyError, ValueError):
            continue
    return sorted(entries, key=lambda e: e["date"])


def upsert(entry: dict) -> None:
    """同じ日付の行を置き換えて追記する。

    同じ日に2回放送を作り直したときや、push衝突でやり直したときに
    履歴が二重にならないよう、追記ではなく上書きにしている。
    """
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    kept = []
    if HISTORY_PATH.exists():
        for line in HISTORY_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                if json.loads(line).get("date") == entry["date"]:
                    continue
            except json.JSONDecodeError:
                pass
            kept.append(line)
    kept.append(json.dumps(entry, ensure_ascii=False))
    HISTORY_PATH.write_text("\n".join(kept) + "\n", encoding="utf-8")


def to_prompt(entries: list[dict], max_topics: int) -> str:
    if not entries:
        return "（履歴なし。今回が初回の放送です。）"
    lines, count = [], 0
    for e in reversed(entries):
        series = e.get("series")
        tag = f" ※連載 part{series['part']}" if series else ""
        lines.append(f"[{e['date']}／{e.get('genre', '')}] {e.get('episode_title', '')}{tag}")
        for t in e.get("topics", []):
            if count >= max_topics:
                break
            lines.append(f"    - {t}")
            count += 1
        for c in e.get("chapter_titles", []):
            lines.append(f"    章: {c}")
        if count >= max_topics:
            break
    return "\n".join(lines)
