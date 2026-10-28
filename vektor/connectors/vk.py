"""Сбор стены сообщества или страницы VK через официальный API (wall.get).

Нужен сервисный ключ приложения VK (бесплатно: vk.com/apps?act=manage →
создать приложение → «Сервисный ключ доступа»). Ключ передаётся через
переменную окружения VK_SERVICE_TOKEN или поле в интерфейсе.
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from typing import Optional

import requests

from ..models import Channel, Post

API = "https://api.vk.com/method/{method}"
API_VERSION = "5.199"


class VKError(RuntimeError):
    pass


def normalize_domain(raw: str) -> str:
    s = raw.strip()
    s = re.sub(r"^https?://", "", s)
    s = re.sub(r"^(m\.)?vk\.(com|ru)/", "", s)
    s = s.lstrip("@").split("/")[0].split("?")[0]
    if not s:
        raise VKError(f"Некорректный адрес VK: {raw!r}")
    return s


def _call(method: str, token: str, session: requests.Session, **params) -> dict | list:
    params.update({"access_token": token, "v": API_VERSION})
    resp = session.get(API.format(method=method), params=params, timeout=20)
    resp.raise_for_status()
    data = resp.json()
    if "error" in data:
        err = data["error"]
        raise VKError(f"VK API {method}: {err.get('error_code')} {err.get('error_msg')}")
    return data["response"]


_ATTACH_MAP = {
    "photo": "photo",
    "video": "video",
    "clip": "video",
    "doc": "document",
    "poll": "poll",
    "audio": "audio",
    "podcast": "audio",
    "link": "link",
    "article": "link",
}


def media_type_from_attachments(attachments: list[dict] | None) -> str:
    if not attachments:
        return "text"
    kinds = [_ATTACH_MAP.get(a.get("type", ""), "other") for a in attachments]
    for preferred in ("video", "poll", "photo", "audio", "document", "link"):
        if preferred in kinds:
            return preferred
    return "other"


def post_from_item(item: dict, domain: str) -> Post:
    owner_id, pid = item.get("owner_id"), item.get("id")
    return Post(
        platform="vk",
        channel=domain,
        post_id=str(pid),
        date=datetime.fromtimestamp(int(item["date"]), tz=timezone.utc),
        text=item.get("text", "") or "",
        views=(item.get("views") or {}).get("count"),
        reactions=(item.get("likes") or {}).get("count", 0) or 0,
        comments=(item.get("comments") or {}).get("count", 0) or 0,
        reposts=(item.get("reposts") or {}).get("count", 0) or 0,
        media_type=media_type_from_attachments(item.get("attachments")),
        url=f"https://vk.com/wall{owner_id}_{pid}",
        is_pinned=bool(item.get("is_pinned")),
    )


def _owner_info(domain: str, token: str, session: requests.Session) -> tuple[str, Optional[int]]:
    """Название и число подписчиков. Сначала пробуем как сообщество, потом как пользователя."""
    try:
        resp = _call("groups.getById", token, session, group_id=domain, fields="members_count")
        groups = resp.get("groups", []) if isinstance(resp, dict) else resp
        if groups:
            g = groups[0]
            return g.get("name", domain), g.get("members_count")
    except VKError:
        pass
    try:
        users = _call("users.get", token, session, user_ids=domain, fields="followers_count")
        if users:
            u = users[0]
            return f"{u.get('first_name', '')} {u.get('last_name', '')}".strip(), u.get("followers_count")
    except VKError:
        pass
    return domain, None


def fetch_wall(
    raw_domain: str,
    token: str,
    max_posts: int = 300,
    session: Optional[requests.Session] = None,
    pause: float = 0.35,
    role: str = "author",
    since: Optional[datetime] = None,
) -> Channel:
    if not token:
        raise VKError("Для VK нужен сервисный ключ (VK_SERVICE_TOKEN)")
    domain = normalize_domain(raw_domain)
    sess = session or requests.Session()
    title, subscribers = _owner_info(domain, token, sess)

    posts: list[Post] = []
    offset = 0
    while len(posts) < max_posts:
        count = min(100, max_posts - len(posts))
        resp = _call("wall.get", token, sess, domain=domain, count=count, offset=offset, filter="owner")
        items = resp.get("items", []) if isinstance(resp, dict) else []
        if not items:
            break
        batch = [post_from_item(it, domain) for it in items]
        posts.extend(batch)
        offset += len(items)
        if offset >= resp.get("count", 0):
            break
        if since and any(p.date < since for p in batch if not p.is_pinned):
            break
        time.sleep(pause)

    if since:
        posts = [p for p in posts if p.date >= since or p.is_pinned]
    return Channel(
        platform="vk",
        channel=domain,
        title=title,
        subscribers=subscribers,
        posts=posts[:max_posts],
        role=role,
        source_note=f"VK API wall.get, vk.com/{domain}",
    )
