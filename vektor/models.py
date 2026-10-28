"""Единая модель данных: посты и каналы с любых площадок приводятся к одному виду."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional

import pandas as pd

MEDIA_TYPES = ("text", "photo", "video", "document", "poll", "audio", "link", "other")

MEDIA_LABELS_RU = {
    "text": "текст",
    "photo": "фото",
    "video": "видео",
    "document": "файл",
    "poll": "опрос",
    "audio": "аудио",
    "link": "ссылка",
    "other": "другое",
}


@dataclass
class Post:
    platform: str               # telegram | vk | csv | demo
    channel: str                # идентификатор канала (username / domain)
    post_id: str
    date: datetime              # всегда timezone-aware (UTC)
    text: str = ""
    views: Optional[int] = None
    reactions: int = 0          # лайки / реакции
    comments: int = 0
    reposts: int = 0
    media_type: str = "text"
    url: str = ""
    is_pinned: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Channel:
    platform: str
    channel: str
    title: str = ""
    subscribers: Optional[int] = None
    posts: list[Post] = field(default_factory=list)
    role: str = "author"        # author | competitor
    source_note: str = ""       # откуда данные (для честности отчёта)

    @property
    def display_name(self) -> str:
        return self.title or self.channel

    def to_frame(self) -> pd.DataFrame:
        cols = [f for f in Post.__dataclass_fields__]
        if not self.posts:
            return pd.DataFrame(columns=cols)
        df = pd.DataFrame([p.to_dict() for p in self.posts], columns=cols)
        df["date"] = pd.to_datetime(df["date"], utc=True)
        df = df.sort_values("date").reset_index(drop=True)
        return df

    def summary(self) -> dict:
        return {
            "platform": self.platform,
            "channel": self.channel,
            "title": self.display_name,
            "subscribers": self.subscribers,
            "posts": len(self.posts),
            "role": self.role,
            "source_note": self.source_note,
        }
