"""生成済みの回を docs/ と history/ に反映しなおす。

同じ日に定時実行と手動実行が重なると、両方が docs/ を書いて push が衝突する。
生成物のマージは機械的に解決できないので、衝突したらリモートの最新状態を取り直し、
この回の内容をその上へ「もう一度載せる」ことで解決する。

    python -m src.republish 2026-09-02

原稿・音声はすでに out/ と Releases にあるので、作り直しは発生しない。
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import yaml

from . import history, publish

CONFIG_PATH = Path("config/config.yaml")


def record_path(date: str) -> Path:
    return Path("out") / date / "episode.json"


def save(date: str, record: dict) -> None:
    path = record_path(date)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")


def apply(cfg: dict, record: dict) -> None:
    """docs/ と history/ に、この回の内容を上書きで反映する。"""
    date = record["date"]

    if record.get("meta"):
        scripts_dir = Path("docs/scripts")
        scripts_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy(record["script_path"], scripts_dir / f"{date}.md")
        publish.record_episode(cfg, record["meta"])

    history.upsert(record["history"])


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("使い方: python -m src.republish YYYY-MM-DD", file=sys.stderr)
        return 2

    date = argv[0]
    path = record_path(date)
    if not path.exists():
        print(f"{path} がありません。この回はまだ生成されていません。", file=sys.stderr)
        return 1

    # 反映のみなのでモデルや音声の設定は使わない。環境変数の上書きも不要。
    cfg = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    apply(cfg, json.loads(path.read_text(encoding="utf-8")))
    print(f"{date} の回を docs/ と history/ に反映しました。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
