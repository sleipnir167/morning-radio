"""①目次作成 → ②章ごとの深掘り執筆 → ③結合 の3ステップ生成。"""
from __future__ import annotations

import re
from pathlib import Path

from .llm import LLM
from .sources import news_text, weather_text

PROMPT_DIR = Path("prompts")
WEEKDAY_JA = ["月", "火", "水", "木", "金", "土", "日"]


def _prompt(name: str, **vars_) -> str:
    text = (PROMPT_DIR / name).read_text(encoding="utf-8")
    for key, value in vars_.items():
        text = text.replace("{{" + key + "}}", str(value))
    return text


_HALFWIDTH_KANA = re.compile(r"[｡-ﾟ]")
_FULLWIDTH_LATIN = re.compile(r"[Ａ-Ｚａ-ｚ]+")


def garbled_chars(text: str) -> int:
    """LLMが散発的に混入させる文字化けの箇所数。

    半角カナは口語原稿にまず現れない。全角ラテン文字は ＭｏＭＡ のような連続なら正常だが、
    単独で語中に紛れ込んでいるもの（「とにかｗ目立つ」など）は化けである。
    """
    count = len(_HALFWIDTH_KANA.findall(text))
    count += sum(1 for m in _FULLWIDTH_LATIN.finditer(text) if len(m.group()) == 1)
    return count


def count_chars(text: str) -> int:
    """SE記法と空白を除いた実質の文字数。"""
    stripped = re.sub(r"【SE:[^】]*】", "", text)
    return len(re.sub(r"\s", "", stripped))


def build_outline(llm: LLM, ctx: dict) -> dict:
    system = _prompt(
        "system_outline.md",
        program_title=ctx["program"]["title"],
        personality=ctx["program"]["personality"],
        genre=ctx["genre"],
        chapters=ctx["chapters"],
        chars_per_chapter=ctx["chars_per_chapter"],
    )
    user = f"""# 今日の日付
{ctx['date']}（{ctx['weekday']}曜日）

# 今日のジャンル
{ctx['genre']}

# 今日の天気
{weather_text(ctx['weather'])}

# 最新のニュース見出し（直近2日）
{news_text(ctx['news'])}

# 過去{ctx['lookback_days']}日間に扱ったトピック（重複禁止）
{ctx['history_text']}

以上をもとに、今日の放送回の構成案をJSONで出力してください。"""
    outline = llm.generate_json(system, user, role="outline")
    chapters = outline.get("chapters") or []
    if not chapters:
        raise RuntimeError("目次生成に失敗しました（chaptersが空）")
    for i, ch in enumerate(chapters, start=1):
        ch.setdefault("no", i)
        ch.setdefault("target_chars", ctx["chars_per_chapter"])
    return outline


def write_chapter(llm: LLM, ctx: dict, outline: dict, index: int, previous_tail: str) -> str:
    ch = outline["chapters"][index]
    system = _prompt(
        "system_chapter.md",
        program_title=ctx["program"]["title"],
        personality=ctx["program"]["personality"],
        chapter_no=ch["no"],
        target_chars=ch["target_chars"],
    )

    series_note = ""
    if outline.get("series") and index == 0:
        s = outline["series"]
        series_note = (
            f"\n# 連載情報\nこの回は「{s.get('topic_key')}」の第{s.get('part')}回です。"
            f"前回の要点: {s.get('recap')}\n冒頭で15秒だけ前回を振り返ってから本題に入ってください。\n"
        )

    prev_block = (
        f"\n# 直前の章の終わり（ここから自然に受けること・要約はしない）\n...{previous_tail}\n"
        if previous_tail
        else "\n# 直前の章\nこれが最初の章です。挨拶ではなく情景か問いから始めてください。\n"
    )

    user = f"""# 放送回のタイトル
{outline.get('episode_title', '')}

# この回を貫くテーマ
{outline.get('theme', '')}

# 全体の目次（自分の担当章以外は書かないこと）
{_outline_digest(outline)}
{series_note}{prev_block}
# あなたが今書く章
第{ch['no']}章「{ch.get('title', '')}」
answering: {ch.get('question', '')}
切り口と盛り込む内容: {ch.get('angle', '')}
次章への渡し: {ch.get('bridge_to_next', '')}
目標文字数: 約{ch['target_chars']}字

# 参考にしてよい今日の一次情報
## 天気
{weather_text(ctx['weather'])}

## ニュース見出し
{news_text(ctx['news'][:20])}

第{ch['no']}章の本文だけを書いてください。"""

    text = _clean(llm.generate(system, user))

    if garbled_chars(text) >= 2:
        print(f"      ※ 文字化けを検知（{garbled_chars(text)}箇所）。第{ch['no']}章を書き直します")
        retry = _clean(llm.generate(system, user))
        if garbled_chars(retry) < garbled_chars(text):
            text = retry

    if count_chars(text) < ch["target_chars"] * 0.75:
        text = _expand(llm, system, user, text, ch["target_chars"])
    return text


def _expand(llm: LLM, system: str, user: str, draft: str, target: int) -> str:
    retry_user = (
        f"{user}\n\n# 直前の試作稿（{count_chars(draft)}字。目標の{target}字に届いていません）\n"
        f"{draft}\n\n"
        "上の稿は内容が薄く、尺が足りません。話題を増やすのではなく、"
        "既にある論点の背景・具体例・数字・エピソードを掘り下げて、"
        f"約{target}字まで密度を上げた完成稿を書き直してください。本文だけを出力すること。"
    )
    expanded = _clean(llm.generate(system, retry_user))
    return expanded if count_chars(expanded) > count_chars(draft) else draft


def assemble(llm: LLM, ctx: dict, outline: dict, chapters: list[str]) -> dict:
    system = _prompt(
        "system_assemble.md",
        program_title=ctx["program"]["title"],
        personality=ctx["program"]["personality"],
    )
    digests = "\n\n".join(
        f"【第{i + 1}章 {outline['chapters'][i].get('title', '')}】\n{body}"
        for i, body in enumerate(chapters)
    )
    series_note = ""
    if outline.get("series"):
        s = outline["series"]
        series_note = f"\n# 連載情報\n第{s.get('part')}回。前回の要点: {s.get('recap')}\n"

    user = f"""# 今日の日付
{ctx['date']}（{ctx['weekday']}曜日）

# 今日の天気
{weather_text(ctx['weather'])}

# 放送回タイトル
{outline.get('episode_title', '')}

# テーマ
{outline.get('theme', '')}

# オープニングの方向性
{outline.get('opening_hook', '')}
{series_note}
# 完成済みの各章本文
{digests}

オープニング、章間のつなぎ（{len(chapters) - 1}本）、エンディングをJSONで出力してください。"""

    result = llm.generate_json(system, user, role="assemble")
    result["opening"] = _clean(result.get("opening", ""))
    result["ending"] = _clean(result.get("ending", ""))
    result["bridges"] = [_clean(b) for b in result.get("bridges", [])]
    while len(result["bridges"]) < len(chapters) - 1:
        result["bridges"].append("【SE: 間】")
    return result


def stitch(outline: dict, chapters: list[str], parts: dict) -> str:
    blocks = [parts["opening"]]
    for i, body in enumerate(chapters):
        blocks.append(body)
        if i < len(chapters) - 1:
            blocks.append(parts["bridges"][i])
    blocks.append(parts["ending"])
    return "\n\n".join(b.strip() for b in blocks if b.strip())


def _outline_digest(outline: dict) -> str:
    return "\n".join(
        f"第{c['no']}章「{c.get('title', '')}」— {c.get('question', '')}"
        for c in outline["chapters"]
    )


def _clean(text: str) -> str:
    """Markdown記法や見出しを除去して、読み上げ可能な素のテキストにする。"""
    text = re.sub(r"```[a-zA-Z]*\n?", "", text)
    text = re.sub(r"^\s*#{1,6}\s*", "", text, flags=re.M)
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"\1", text)
    text = re.sub(r"^\s*[-*・]\s+", "", text, flags=re.M)
    text = re.sub(r"^\s*第?\d+\s*章[：:、]?\s*$", "", text, flags=re.M)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
