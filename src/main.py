from __future__ import annotations

import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from . import history, publish
from .llm import LLM
from .pipeline import (
    WEEKDAY_JA,
    assemble,
    build_outline,
    count_chars,
    stitch,
    write_chapter,
)
from .sources import fetch_news, fetch_weather
from .tts import duration_seconds, synthesize

CONFIG_PATH = Path("config/config.yaml")


def load_dotenv() -> None:
    env = Path(".env")
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def load_config() -> dict:
    cfg = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    if os.environ.get("CHAPTERS"):
        cfg["script"]["chapters"] = int(os.environ["CHAPTERS"])
    if os.environ.get("CHARS_PER_CHAPTER"):
        cfg["script"]["chars_per_chapter"] = int(os.environ["CHARS_PER_CHAPTER"])
    if os.environ.get("TTS_VOICE"):
        cfg["tts"]["voice"] = os.environ["TTS_VOICE"]
    if os.environ.get("GDRIVE_FOLDER_ID"):
        cfg["publish"]["gdrive"]["folder_id"] = os.environ["GDRIVE_FOLDER_ID"]
    return cfg


def main() -> int:
    load_dotenv()
    cfg = load_config()
    tz = ZoneInfo(cfg["location"]["timezone"])
    now = datetime.now(tz)
    today = now.strftime("%Y-%m-%d")
    weekday = now.weekday()

    genre = os.environ.get("GENRE") or cfg["genres"][weekday]
    print(f"[1/7] {today}（{WEEKDAY_JA[weekday]}） ジャンル: {genre}")

    weather = fetch_weather(cfg["location"])
    news = fetch_news(cfg["news_queries"].get(genre, [genre]))
    print(f"      天気: {weather['summary']} / ニュース {len(news)}件")

    past = history.load(cfg["history"]["lookback_days"])
    ctx = {
        "program": cfg["program"],
        "date": today,
        "weekday": WEEKDAY_JA[weekday],
        "genre": genre,
        "weather": weather,
        "news": news,
        "chapters": cfg["script"]["chapters"],
        "chars_per_chapter": cfg["script"]["chars_per_chapter"],
        "lookback_days": cfg["history"]["lookback_days"],
        "history_text": history.to_prompt(past, cfg["history"]["max_topics_in_prompt"]),
    }

    llm = LLM(cfg["llm"])
    print(f"[2/7] 目次を作成中（{llm.provider} / {llm.model}）")
    outline = build_outline(llm, ctx)
    print(f"      『{outline.get('episode_title')}』 全{len(outline['chapters'])}章")
    if outline.get("series"):
        print(f"      連載: {outline['series'].get('topic_key')} part{outline['series'].get('part')}")

    bodies: list[str] = []
    for i, ch in enumerate(outline["chapters"]):
        tail = bodies[-1][-220:] if bodies else ""
        print(f"[3/7] 第{ch['no']}章「{ch.get('title')}」を執筆中")
        body = write_chapter(llm, ctx, outline, i, tail)
        print(f"      {count_chars(body)}字")
        bodies.append(body)

    print("[4/7] オープニング・つなぎ・エンディングを生成し結合中")
    parts = assemble(llm, ctx, outline, bodies)
    script = stitch(outline, bodies, parts)
    total = count_chars(script)
    print(f"      総文字数 {total}字")
    if not cfg["script"]["total_chars_min"] <= total <= cfg["script"]["total_chars_max"]:
        print(f"      ※ 目標レンジ {cfg['script']['total_chars_min']}〜{cfg['script']['total_chars_max']}字 から外れています")

    outdir = Path("out") / today
    outdir.mkdir(parents=True, exist_ok=True)
    script_path = outdir / "script.md"
    header = (
        f"# {outline.get('episode_title')}\n\n"
        f"- 放送日: {today}（{WEEKDAY_JA[weekday]}）\n"
        f"- ジャンル: {genre}\n"
        f"- テーマ: {outline.get('theme', '')}\n"
        f"- 総文字数: {total}字\n\n---\n\n"
    )
    script_path.write_text(header + script + "\n", encoding="utf-8")
    (outdir / "outline.json").write_text(
        json.dumps(outline, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"      原稿を書き出しました: {script_path}")

    audio_path = outdir / f"radio-{today}.mp3"
    if os.environ.get("SKIP_TTS") == "1":
        print("[5/7] SKIP_TTS=1 のため音声合成をスキップ")
        return _finish(cfg, today, outline, genre, total, script_path, None, past)

    print(f"[5/7] 音声合成中（{cfg['tts']['voice']}）")
    synthesize(script, audio_path, cfg["tts"])
    print(f"      {audio_path} / {audio_path.stat().st_size / 1_048_576:.1f}MB / {duration_seconds(audio_path)}秒")

    return _finish(cfg, today, outline, genre, total, script_path, audio_path, past)


def _finish(cfg, today, outline, genre, total, script_path, audio_path, past) -> int:
    tag = f"ep-{today.replace('-', '')}"

    if audio_path and cfg["publish"]["podcast"]["enabled"]:
        print("[6/7] Podcast RSS と閲覧ページを更新中")
        scripts_dir = Path("docs/scripts")
        scripts_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy(script_path, scripts_dir / f"{today}.md")
        publish.record_episode(
            cfg,
            {
                "date": today,
                "title": outline.get("episode_title", today),
                "summary": outline.get("theme", ""),
                "genre": genre,
                "audio_url": publish.release_url(tag, audio_path.name),
                "script_url": f"scripts/{today}.md",
                "size": audio_path.stat().st_size,
                "duration": duration_seconds(audio_path),
                "published": publish.now_iso(),
            },
        )

    if audio_path and cfg["publish"]["gdrive"]["enabled"] and cfg["publish"]["gdrive"].get("folder_id"):
        print("[6/7] Google Drive にアップロード中")
        try:
            links = publish.upload_to_drive(
                [audio_path, script_path], cfg["publish"]["gdrive"]["folder_id"]
            )
            for link in links:
                print(f"      {link}")
        except Exception as exc:  # 配信の一部失敗で全体を落とさない
            print(f"      ※ Driveアップロードに失敗: {exc}", file=sys.stderr)

    print("[7/7] 履歴を記録中")
    history.append(
        {
            "date": today,
            "genre": genre,
            "episode_title": outline.get("episode_title", ""),
            "theme": outline.get("theme", ""),
            "topics": outline.get("topics", []),
            "chapter_titles": [c.get("title", "") for c in outline["chapters"]],
            "series": outline.get("series"),
            "total_chars": total,
        }
    )

    gh_out = os.environ.get("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a", encoding="utf-8") as f:
            f.write(f"tag={tag}\n")
            f.write(f"date={today}\n")
            f.write(f"title={outline.get('episode_title', '')}\n")
            f.write(f"script_path={script_path}\n")
            f.write(f"audio_path={audio_path or ''}\n")
    print("完了しました。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
