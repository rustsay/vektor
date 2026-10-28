"""Импорт из CSV — для любой площадки (YouTube Studio, Instagram, Дзен, TikTok и т.д.).

Минимально нужны колонки с датой и просмотрами. Названия колонок
распознаются по синонимам на русском и английском.
"""
from __future__ import annotations

import io
from typing import Optional

import pandas as pd

from ..models import Channel, Post

SYNONYMS = {
    "date": ["date", "datetime", "published", "published_at", "дата", "дата публикации", "время"],
    "text": ["text", "title", "caption", "текст", "заголовок", "название", "описание"],
    "views": ["views", "view_count", "impressions", "reach", "просмотры", "охват", "показы"],
    "reactions": ["likes", "reactions", "like_count", "лайки", "реакции", "нравится"],
    "comments": ["comments", "comment_count", "комментарии", "комменты"],
    "reposts": ["reposts", "shares", "forwards", "репосты", "поделились", "пересылки"],
    "media_type": ["media_type", "type", "format", "формат", "тип"],
    "url": ["url", "link", "ссылка"],
    "post_id": ["id", "post_id", "video_id", "ид"],
}

MEDIA_SYNONYMS = {
    "video": ["video", "видео", "reels", "shorts", "клип", "clip", "ролик"],
    "photo": ["photo", "image", "фото", "картинка", "изображение", "carousel", "карусель"],
    "text": ["text", "текст", "пост"],
    "poll": ["poll", "опрос"],
    "audio": ["audio", "аудио", "podcast", "подкаст"],
    "document": ["document", "file", "файл", "документ"],
    "link": ["link", "ссылка", "article", "статья"],
}


class CSVError(ValueError):
    pass


def _map_columns(columns: list[str]) -> dict[str, str]:
    lower = {c.lower().strip(): c for c in columns}
    mapping = {}
    for field, names in SYNONYMS.items():
        for n in names:
            if n in lower:
                mapping[field] = lower[n]
                break
    return mapping


def _media(value) -> str:
    s = str(value or "").strip().lower()
    for media, names in MEDIA_SYNONYMS.items():
        if any(n in s for n in names):
            return media
    return "text" if not s else "other"


def _int(value) -> int:
    try:
        if pd.isna(value):
            return 0
        return int(float(str(value).replace(" ", "").replace(" ", "").replace(",", ".")))
    except (TypeError, ValueError):
        return 0


def load_csv(
    data: bytes | str,
    channel_name: str,
    subscribers: Optional[int] = None,
    role: str = "author",
) -> Channel:
    raw = data.decode("utf-8-sig") if isinstance(data, bytes) else data
    sep = ";" if raw.count(";") > raw.count(",") else ","
    df = pd.read_csv(io.StringIO(raw), sep=sep)
    mapping = _map_columns(list(df.columns))
    if "date" not in mapping or "views" not in mapping:
        raise CSVError(
            "В CSV нужны как минимум колонки с датой и просмотрами "
            "(например: date, views). Найдено: " + ", ".join(df.columns)
        )
    raw_dates = df[mapping["date"]].astype(str).str.strip()
    # ISO (2026-10-01) разбираем как год-месяц-день, остальное (01.10.2026) — как день первым
    iso = raw_dates.str.match(r"^\d{4}-\d{1,2}-\d{1,2}").mean() > 0.5
    dates = pd.to_datetime(raw_dates, errors="coerce", dayfirst=not iso, format="mixed")
    if dates.dt.tz is None:
        dates = dates.dt.tz_localize("Europe/Moscow")
    dates = dates.dt.tz_convert("UTC")

    posts = []
    for i, row in df.iterrows():
        if pd.isna(dates.iloc[i]):
            continue
        posts.append(
            Post(
                platform="csv",
                channel=channel_name,
                post_id=str(row[mapping["post_id"]]) if "post_id" in mapping else str(i + 1),
                date=dates.iloc[i].to_pydatetime(),
                text=str(row[mapping["text"]]) if "text" in mapping and not pd.isna(row[mapping["text"]]) else "",
                views=_int(row[mapping["views"]]),
                reactions=_int(row[mapping["reactions"]]) if "reactions" in mapping else 0,
                comments=_int(row[mapping["comments"]]) if "comments" in mapping else 0,
                reposts=_int(row[mapping["reposts"]]) if "reposts" in mapping else 0,
                media_type=_media(row[mapping["media_type"]]) if "media_type" in mapping else "text",
                url=str(row[mapping["url"]]) if "url" in mapping and not pd.isna(row[mapping["url"]]) else "",
            )
        )
    if not posts:
        raise CSVError("В CSV не найдено ни одной строки с корректной датой")
    return Channel(
        platform="csv",
        channel=channel_name,
        title=channel_name,
        subscribers=subscribers,
        posts=posts,
        role=role,
        source_note="загруженный CSV",
    )
