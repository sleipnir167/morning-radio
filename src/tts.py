"""Edge-TTS による音声合成。【SE: 間】は実際の無音として挿入する。"""
from __future__ import annotations

import asyncio
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import edge_tts

PAUSE_MARK = "【SE: 間】"
SHIFT_MARK = "【SE: 転換】"
_MARK_RE = re.compile(r"【SE:\s*(間|転換)\s*】")
_READING_RE = re.compile(r"([一-龥々]+)（([ぁ-んァ-ヴー]+)）")


def ensure_voice(voice: str) -> None:
    """使えない音声名なら、原稿を作る前に止める。

    Edge-TTS が扱えるのは Azure の全音声ではなく、その一部だけ。
    合成は最後の工程なので、ここで弾かないと10分と10リクエストを捨てることになる。
    """
    names = [v["ShortName"] for v in asyncio.run(edge_tts.list_voices())]
    if voice in names:
        return
    same_lang = sorted(n for n in names if n.startswith(voice.split("-")[0] + "-"))
    raise RuntimeError(
        f"音声「{voice}」は Edge-TTS では使えません。使えるのは: {', '.join(same_lang) or '（該当なし）'}"
    )


def to_speech_text(script: str) -> str:
    """原稿を読み上げ用に正規化する（ルビ注記の重複読みを防ぐ）。"""
    text = _READING_RE.sub(r"\2", script)
    text = re.sub(r"[*_`#>]", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def _segments(text: str, chunk_chars: int) -> list[tuple[str, float]]:
    """(読み上げるテキスト, その直後に入れる無音秒数) のリスト。"""
    out: list[tuple[str, float]] = []
    pos = 0
    for m in _MARK_RE.finditer(text):
        out.append((text[pos : m.start()], 1.0 if m.group(1) == "間" else 1.8))
        pos = m.end()
    out.append((text[pos:], 0.0))

    chunked: list[tuple[str, float]] = []
    for body, pause in out:
        pieces = _split(body.strip(), chunk_chars)
        if not pieces:
            # マーカーが連続したときは無音を足し算せず、長いほうに寄せる
            if pause and chunked:
                chunked[-1] = (chunked[-1][0], max(chunked[-1][1], pause))
            continue
        for piece in pieces[:-1]:
            chunked.append((piece, 0.0))
        chunked.append((pieces[-1], pause))
    return chunked


def _split(body: str, limit: int) -> list[str]:
    if not body:
        return []
    pieces, buf = [], ""
    for para in re.split(r"\n\s*\n", body):
        para = para.strip()
        if not para:
            continue
        if len(buf) + len(para) + 1 > limit and buf:
            pieces.append(buf)
            buf = para
        else:
            buf = f"{buf}\n{para}" if buf else para
    if buf:
        pieces.append(buf)

    final = []
    for piece in pieces:
        while len(piece) > limit * 1.6:
            cut = piece.rfind("。", 0, limit)
            cut = cut + 1 if cut > limit * 0.3 else limit
            final.append(piece[:cut])
            piece = piece[cut:]
        final.append(piece)
    return [p for p in final if p.strip()]


async def _synth(text: str, out: Path, cfg: dict) -> None:
    comm = edge_tts.Communicate(
        text,
        cfg["voice"],
        rate=cfg.get("rate", "+0%"),
        pitch=cfg.get("pitch", "+0Hz"),
        volume=cfg.get("volume", "+0%"),
    )
    await comm.save(str(out))


def synthesize(script: str, out_path: Path, cfg: dict) -> Path:
    text = to_speech_text(script)
    segments = _segments(text, int(cfg.get("chunk_chars", 1200)))
    ffmpeg = shutil.which("ffmpeg")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        pieces: list[Path] = []
        for i, (body, pause) in enumerate(segments):
            part = tmpdir / f"p{i:03d}.mp3"
            asyncio.run(_synth(body, part, cfg))
            if not part.exists() or part.stat().st_size == 0:
                raise RuntimeError(f"TTSがセグメント{i}で空の音声を返しました")
            pieces.append(part)
            if pause > 0 and ffmpeg:
                pieces.append(_silence(ffmpeg, tmpdir / f"s{i:03d}.mp3", pause))

        if ffmpeg:
            listing = tmpdir / "list.txt"
            listing.write_text(
                "\n".join(f"file '{p.as_posix()}'" for p in pieces), encoding="utf-8"
            )
            subprocess.run(
                [ffmpeg, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
                 "-i", str(listing), "-c", "copy", str(out_path)],
                check=True,
            )
        else:
            with out_path.open("wb") as dst:
                for p in pieces:
                    dst.write(p.read_bytes())
    return out_path


def _silence(ffmpeg: str, path: Path, seconds: float) -> Path:
    subprocess.run(
        [ffmpeg, "-y", "-loglevel", "error", "-f", "lavfi",
         "-i", "anullsrc=r=24000:cl=mono", "-t", f"{seconds:.2f}",
         "-c:a", "libmp3lame", "-b:a", "48k", str(path)],
        check=True,
    )
    return path


def duration_seconds(path: Path) -> int:
    ffprobe = shutil.which("ffprobe")
    if ffprobe:
        res = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True, text=True,
        )
        try:
            return int(float(res.stdout.strip()))
        except ValueError:
            pass
    return int(path.stat().st_size * 8 / 48000)  # 48kbps 前提の概算
