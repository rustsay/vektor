"""Сбор публичного Telegram-канала через веб-превью t.me/s/<username>.

Не требует токенов и аккаунта: работает только для публичных каналов,
у которых включён предпросмотр. Из превью доступны текст, дата, просмотры,
тип медиа и (у большинства каналов) реакции. Комментарии и репосты
в превью не отображаются, поэтому для Telegram они равны нулю.
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from typing import Optional

import requests
from lxml import html as lxml_html

from ..models import Channel, Post

BASE = "https://t.me/s/{username}"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
}

_SUFFIX = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}


class TelegramError(RuntimeError):
    pass


def normalize_username(raw: str) -> str:
    """Принимает @name, t.me/name, https://t.me/s/name → name."""
    s = raw.strip()
    s = re.sub(r"^https?://", "", s)
    s = re.sub(r"^(www\.)?(t\.me|telegram\.me)/(s/)?", "", s)
    s = s.lstrip("@").split("/")[0].split("?")[0]
    if not re.fullmatch(r"[A-Za-z0-9_]{4,64}", s):
        raise TelegramError(f"Некорректное имя канала: {raw!r}")
    return s


def parse_count(raw: Optional[str]) -> Optional[int]:
    """'1.2K' → 1200, '3,4M' → 3400000, '987' → 987."""
    if raw is None:
        return None
    s = raw.strip().replace(" ", "").replace(" ", "")
    if not s:
        return None
    m = re.fullmatch(r"([\d.,]+)([KMB]?)", s, flags=re.I)
    if not m:
        return None
    num, suf = m.groups()
    num = num.replace(",", ".")
    # «1.234» без суффикса — это разделитель тысяч
    if not suf and num.count(".") >= 1 and len(num.split(".")[-1]) == 3:
        num = num.replace(".", "")
    try:
        value = float(num)
    except ValueError:
        return None
    return int(round(value * _SUFFIX.get(suf.upper(), 1)))


def _text_of(el) -> str:
    # <br> → перенос строки, остальное — текст
    for br in el.xpath(".//br"):
        br.tail = "\n" + (br.tail or "")
    return el.text_content().strip()


def _media_type(msg) -> str:
    checks = [
        ("video", ".//*[contains(@class,'tgme_widget_message_video_player') or contains(@class,'tgme_widget_message_roundvideo')]"),
        ("photo", ".//*[contains(@class,'tgme_widget_message_photo_wrap')]"),
        ("poll", ".//*[contains(@class,'tgme_widget_message_poll')]"),
        ("audio", ".//*[contains(@class,'tgme_widget_message_voice') or contains(@class,'tgme_widget_message_audio')]"),
        ("document", ".//*[contains(@class,'tgme_widget_message_document')]"),
        ("link", ".//*[contains(@class,'tgme_widget_message_link_preview')]"),
    ]
    for name, xp in checks:
        if msg.xpath(xp):
            return name
    return "text"


def _reactions(msg) -> int:
    total = 0
    for r in msg.xpath(".//*[contains(@class,'tgme_widget_message_reactions')]//*[contains(@class,'tgme_reaction')]"):
        # число — последний текстовый фрагмент внутри реакции
        txt = "".join(r.xpath("./text()")).strip() or r.text_content().strip()
        m = re.search(r"([\d.,]+[KMB]?)\s*$", txt, flags=re.I)
        if m:
            total += parse_count(m.group(1)) or 0
    return total


def parse_page(page_html: str, username: str) -> tuple[dict, list[Post]]:
    """Разбирает одну страницу t.me/s. Возвращает (информация о канале, посты)."""
    doc = lxml_html.fromstring(page_html)

    info: dict = {"title": "", "subscribers": None}
    title = doc.xpath("//*[contains(@class,'tgme_channel_info_header_title')]")
    if title:
        info["title"] = title[0].text_content().strip()
    for counter in doc.xpath("//*[contains(@class,'tgme_channel_info_counter')]"):
        ctype = "".join(counter.xpath(".//*[contains(@class,'counter_type')]//text()")).strip().lower()
        cval = "".join(counter.xpath(".//*[contains(@class,'counter_value')]//text()")).strip()
        if ctype.startswith("subscriber") or ctype.startswith("подписч"):
            info["subscribers"] = parse_count(cval)

    posts: list[Post] = []
    for msg in doc.xpath("//div[contains(concat(' ', normalize-space(@class), ' '), ' tgme_widget_message ')][@data-post]"):
        data_post = msg.get("data-post", "")
        if "/" not in data_post:
            continue
        pid = data_post.split("/")[-1]
        if msg.xpath(".//*[contains(@class,'service_message')]") or "service_message" in (msg.get("class") or ""):
            continue

        views_el = msg.xpath(".//*[contains(@class,'tgme_widget_message_views')]")
        views = parse_count(views_el[0].text_content()) if views_el else None

        t = msg.xpath(".//*[contains(@class,'tgme_widget_message_date')]//time[@datetime]")
        if not t:
            continue
        date = datetime.fromisoformat(t[0].get("datetime").replace("Z", "+00:00"))
        if date.tzinfo is None:
            date = date.replace(tzinfo=timezone.utc)
        date = date.astimezone(timezone.utc)

        # основной текст (не цитата из ответа)
        text_el = msg.xpath(".//*[contains(@class,'js-message_text')]")
        text = _text_of(text_el[0]) if text_el else ""

        posts.append(
            Post(
                platform="telegram",
                channel=username,
                post_id=pid,
                date=date,
                text=text,
                views=views,
                reactions=_reactions(msg),
                comments=0,
                reposts=0,
                media_type=_media_type(msg),
                url=f"https://t.me/{username}/{pid}",
            )
        )
    return info, posts


def fetch_channel(
    raw_username: str,
    max_posts: int = 300,
    session: Optional[requests.Session] = None,
    pause: float = 1.0,
    role: str = "author",
    since: Optional[datetime] = None,
) -> Channel:
    """since — собирать посты не старше этой даты (чтобы период конкурентов совпадал с периодом автора)."""
    username = normalize_username(raw_username)
    sess = session or requests.Session()
    url = BASE.format(username=username)
    seen: dict[str, Post] = {}
    info: dict = {}
    before: Optional[int] = None

    for _ in range(max(1, max_posts // 15) + 5):
        params = {"before": before} if before else None
        resp = sess.get(url, params=params, headers=HEADERS, timeout=20)
        if resp.status_code != 200:
            raise TelegramError(f"t.me ответил {resp.status_code} для канала @{username}")
        page_info, posts = parse_page(resp.text, username)
        if not info:
            info = page_info
            if not posts and not page_info.get("title"):
                raise TelegramError(
                    f"Канал @{username} не найден или у него отключён веб-предпросмотр"
                )
        new = [p for p in posts if p.post_id not in seen]
        if not new:
            break
        for p in new:
            seen[p.post_id] = p
        if len(seen) >= max_posts:
            break
        if since and min(p.date for p in new) < since:
            break
        min_id = min(int(p.post_id) for p in new if p.post_id.isdigit())
        if min_id <= 1:
            break
        before = min_id
        time.sleep(pause)

    posts_sorted = sorted(seen.values(), key=lambda p: p.date, reverse=True)
    if since:
        posts_sorted = [p for p in posts_sorted if p.date >= since]
    posts_sorted = posts_sorted[:max_posts]
    return Channel(
        platform="telegram",
        channel=username,
        title=info.get("title", ""),
        subscribers=info.get("subscribers"),
        posts=posts_sorted,
        role=role,
        source_note=f"публичное веб-превью t.me/s/{username}",
    )
