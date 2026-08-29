"""配信: Podcast RSS（GitHub Pages）と Google Drive アップロード。"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

DOCS = Path("docs")
EPISODES_JSON = DOCS / "episodes.json"
FEED_XML = DOCS / "feed.xml"


def release_url(tag: str, filename: str) -> str:
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    return f"https://github.com/{repo}/releases/download/{tag}/{filename}"


def record_episode(cfg: dict, meta: dict) -> None:
    DOCS.mkdir(parents=True, exist_ok=True)
    episodes = json.loads(EPISODES_JSON.read_text(encoding="utf-8")) if EPISODES_JSON.exists() else []
    episodes = [e for e in episodes if e["date"] != meta["date"]]
    episodes.append(meta)
    episodes.sort(key=lambda e: e["date"], reverse=True)
    episodes = episodes[: int(cfg["publish"]["podcast"].get("max_episodes", 60))]
    EPISODES_JSON.write_text(
        json.dumps(episodes, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _write_feed(cfg, episodes)
    _write_index(cfg, episodes)


def _site_base(cfg: dict) -> str:
    configured = cfg["publish"]["podcast"].get("base_url")
    if configured:
        return configured.rstrip("/")
    repo = os.environ.get("GITHUB_REPOSITORY", "owner/repo")
    owner, name = repo.split("/", 1)
    return f"https://{owner}.github.io/{name}"


def _write_feed(cfg: dict, episodes: list[dict]) -> None:
    from feedgen.feed import FeedGenerator

    base = _site_base(cfg)
    fg = FeedGenerator()
    fg.load_extension("podcast")
    fg.id(f"{base}/feed.xml")
    fg.title(cfg["program"]["title"])
    fg.subtitle(cfg["program"].get("subtitle", ""))
    fg.author({"name": cfg["program"]["personality"]})
    # feedgen は RSS の <link> に最後の要素を使うため、alternate を末尾に置く
    fg.link([{"href": f"{base}/feed.xml", "rel": "self"}, {"href": base, "rel": "alternate"}])
    fg.language("ja")
    fg.description(cfg["program"].get("subtitle") or cfg["program"]["title"])
    fg.podcast.itunes_author(cfg["program"]["personality"])
    fg.podcast.itunes_category("Society & Culture")
    fg.podcast.itunes_explicit("no")

    for ep in reversed(episodes):  # feedgen は追加順が逆になる
        fe = fg.add_entry()
        fe.id(ep["audio_url"])
        fe.title(f"{ep['date']} {ep['title']}")
        fe.description(ep.get("summary", ""))
        fe.enclosure(ep["audio_url"], str(ep.get("size", 0)), "audio/mpeg")
        fe.published(datetime.fromisoformat(ep["published"]))
        fe.podcast.itunes_duration(_hhmmss(ep.get("duration", 0)))

    fg.rss_file(str(FEED_XML), pretty=True)


def _hhmmss(seconds: int) -> str:
    seconds = int(seconds)
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def _write_index(cfg: dict, episodes: list[dict]) -> None:
    base = _site_base(cfg)
    items = "\n".join(
        f"""    <li>
      <div class="d">{e['date']}</div>
      <div class="t">{e['title']}</div>
      <p>{e.get('summary', '')}</p>
      <audio controls preload="none" src="{e['audio_url']}"></audio>
      <a href="{e['script_url']}">原稿を読む</a>
    </li>"""
        for e in episodes
    )
    DOCS.joinpath("index.html").write_text(
        f"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{cfg['program']['title']}</title>
<style>
body{{font-family:system-ui,sans-serif;max-width:720px;margin:0 auto;padding:24px;line-height:1.7;color:#222}}
h1{{font-size:1.4rem}} ul{{list-style:none;padding:0}}
li{{border-top:1px solid #ddd;padding:20px 0}}
.d{{color:#888;font-size:.85rem}} .t{{font-weight:700;font-size:1.05rem;margin:4px 0}}
audio{{width:100%;margin:8px 0}} a{{color:#06c}}
.feed{{display:inline-block;background:#222;color:#fff;padding:8px 14px;border-radius:6px;text-decoration:none}}
</style></head><body>
<h1>{cfg['program']['title']}</h1>
<p>{cfg['program'].get('subtitle', '')}</p>
<p><a class="feed" href="{base}/feed.xml">Podcast RSS を購読</a></p>
<ul>
{items}
</ul>
</body></html>
""",
        encoding="utf-8",
    )


def upload_to_drive(files: list[Path], folder_id: str) -> list[str]:
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload

    raw = os.environ.get("GDRIVE_SERVICE_ACCOUNT_JSON")
    if not raw:
        raise RuntimeError("GDRIVE_SERVICE_ACCOUNT_JSON が設定されていません")
    creds = service_account.Credentials.from_service_account_info(
        json.loads(raw), scopes=["https://www.googleapis.com/auth/drive.file"]
    )
    service = build("drive", "v3", credentials=creds, cache_discovery=False)

    links = []
    for path in files:
        mime = "audio/mpeg" if path.suffix == ".mp3" else "text/markdown"
        created = (
            service.files()
            .create(
                body={"name": path.name, "parents": [folder_id]},
                media_body=MediaFileUpload(str(path), mimetype=mime, resumable=True),
                fields="id,webViewLink",
                supportsAllDrives=True,
            )
            .execute()
        )
        links.append(created.get("webViewLink", created["id"]))
    return links


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
