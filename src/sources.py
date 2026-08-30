"""天気（Open-Meteo）とニュース（Google News RSS）の取得。どちらもAPIキー不要。"""
from __future__ import annotations

import time
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime

import requests

# Google News RSS も Open-Meteo も、無料で使わせてもらっている分ときどき 503 を返す。
# 毎朝の自動実行が一度の瞬断で落ちないよう、間隔を空けて数回試す。
_BACKOFF = (3, 8, 20)


def _get(url: str, **kwargs) -> requests.Response:
    last: Exception | None = None
    for i, wait in enumerate((*_BACKOFF, None)):
        try:
            res = requests.get(url, timeout=60, **kwargs)
            res.raise_for_status()
            return res
        except requests.RequestException as exc:
            last = exc
            if wait is None:
                break
            print(f"      取得に失敗（{exc.__class__.__name__}）。{wait}秒後に再試行します")
            time.sleep(wait)
    raise last  # type: ignore[misc]

WMO = {
    0: "快晴", 1: "晴れ", 2: "薄曇り", 3: "曇り",
    45: "霧", 48: "霧氷をともなう霧",
    51: "弱い霧雨", 53: "霧雨", 55: "強い霧雨",
    61: "弱い雨", 63: "雨", 65: "強い雨",
    66: "冷たい雨", 67: "強い冷たい雨",
    71: "弱い雪", 73: "雪", 75: "強い雪", 77: "細かい雪",
    80: "にわか雨", 81: "強いにわか雨", 82: "激しいにわか雨",
    85: "にわか雪", 86: "強いにわか雪",
    95: "雷雨", 96: "雹をともなう雷雨", 99: "激しい雹をともなう雷雨",
}


def fetch_weather(loc: dict) -> dict:
    res = _get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": loc["latitude"],
            "longitude": loc["longitude"],
            "timezone": loc["timezone"],
            "forecast_days": 1,
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,sunrise",
            "hourly": "temperature_2m,precipitation_probability,apparent_temperature",
        },
    )
    data = res.json()
    daily, hourly = data["daily"], data["hourly"]

    morning = []
    for hour in loc.get("morning_hours", [6, 7, 8, 9]):
        for i, stamp in enumerate(hourly["time"]):
            if datetime.fromisoformat(stamp).hour == hour:
                morning.append(
                    {
                        "hour": hour,
                        "temp": hourly["temperature_2m"][i],
                        "feels_like": hourly["apparent_temperature"][i],
                        "rain_prob": hourly["precipitation_probability"][i],
                    }
                )
                break

    return {
        "location": loc["name"],
        "summary": WMO.get(daily["weather_code"][0], "不明"),
        "temp_max": daily["temperature_2m_max"][0],
        "temp_min": daily["temperature_2m_min"][0],
        "rain_prob_max": daily["precipitation_probability_max"][0],
        "sunrise": daily["sunrise"][0],
        "morning": morning,
    }


def weather_text(w: dict) -> str:
    lines = [
        f"地点: {w['location']}",
        f"天気: {w['summary']}",
        f"最高気温 {w['temp_max']}度 / 最低気温 {w['temp_min']}度 / 日中の最大降水確率 {w['rain_prob_max']}%",
        f"日の出: {w['sunrise'][11:16]}",
    ]
    for m in w["morning"]:
        lines.append(
            f"{m['hour']}時台: 気温 {m['temp']}度（体感 {m['feels_like']}度）、降水確率 {m['rain_prob']}%"
        )
    return "\n".join(lines)


def _rss(url: str, limit: int) -> list[dict]:
    res = _get(url, headers={"User-Agent": "Mozilla/5.0 (morning-radio)"})
    root = ET.fromstring(res.content)
    items = []
    for item in list(root.iterfind(".//item"))[:limit]:
        title = (item.findtext("title") or "").strip()
        source = item.findtext("source") or ""
        pub = (item.findtext("pubDate") or "").strip()
        if title:
            items.append({"title": title, "source": source.strip(), "published": pub})
    return items


def fetch_news(queries: list[str], per_query: int = 8, headlines: int = 10) -> list[dict]:
    try:
        articles = _rss("https://news.google.com/rss?hl=ja&gl=JP&ceid=JP:ja", headlines)
    except Exception:  # 総合トップが落ちていてもジャンル別検索だけで番組は作れる
        print("      ※ ニュースの総合トップを取得できませんでした")
        articles = []

    for q in queries:
        encoded = urllib.parse.quote(f"{q} when:2d")
        try:
            articles += _rss(
                f"https://news.google.com/rss/search?q={encoded}&hl=ja&gl=JP&ceid=JP:ja",
                per_query,
            )
        except Exception:  # 個別クエリの失敗で全体を止めない
            continue

    seen, unique = set(), []
    for a in articles:
        if a["title"] in seen:
            continue
        seen.add(a["title"])
        unique.append(a)
    return unique


def news_text(articles: list[dict]) -> str:
    if not articles:
        return "（今日は取得できませんでした。ジャンルとテーマだけで構成してください）"
    return "\n".join(
        f"- {a['title']}" + (f"（{a['source']}）" if a["source"] else "") for a in articles
    )
