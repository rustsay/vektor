"""Метрики канала.

Ключевая идея — «индекс виральности»: охват поста делится на медиану
охвата предыдущих постов того же канала. Так мы сравниваем посты
честно, даже если канал за полгода вырос в три раза, и можем сравнивать
каналы разного размера между собой.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

import numpy as np
import pandas as pd
from scipy.stats import mannwhitneyu, theilslopes

from ..models import Channel, MEDIA_LABELS_RU

TZ = "Europe/Moscow"
FRESH_HOURS = 72          # свежие посты ещё набирают охват — исключаем из сравнения
BASELINE_WINDOW = 15      # окно скользящей медианы
MIN_BUCKET = 3            # минимум постов в группе, чтобы о ней говорить

WEEKDAYS_RU = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
SLOTS = [(0, 6, "00–06"), (6, 9, "06–09"), (9, 12, "09–12"), (12, 15, "12–15"),
         (15, 18, "15–18"), (18, 21, "18–21"), (21, 24, "21–24")]
LENGTH_BUCKETS = [(0, 300, "до 300 зн."), (300, 1000, "300–1000 зн."), (1000, 10**9, "от 1000 зн.")]


def _slot(hour: int) -> str:
    for lo, hi, name in SLOTS:
        if lo <= hour < hi:
            return name
    return SLOTS[-1][2]


def _length_bucket(n: int) -> str:
    for lo, hi, name in LENGTH_BUCKETS:
        if lo <= n < hi:
            return name
    return LENGTH_BUCKETS[-1][2]


def prepare_frame(channel: Channel, now: Optional[datetime] = None) -> pd.DataFrame:
    """Посты канала + производные признаки."""
    df = channel.to_frame()
    if df.empty:
        return df
    now = now or datetime.now(timezone.utc)
    df = df[df["views"].notna()].copy()
    df["views"] = df["views"].astype(float)
    df["is_pinned"] = df["is_pinned"].fillna(False).astype(bool)
    df["local"] = df["date"].dt.tz_convert(TZ)
    df["weekday"] = df["local"].dt.weekday
    df["hour"] = df["local"].dt.hour
    df["slot"] = df["hour"].map(_slot)
    df["text"] = df["text"].fillna("")
    df["length"] = df["text"].str.len()
    df["length_bucket"] = df["length"].map(_length_bucket)
    df["engagement"] = df[["reactions", "comments", "reposts"]].fillna(0).sum(axis=1)
    df["er"] = np.where(df["views"] > 0, df["engagement"] / df["views"], np.nan)
    df["age_days"] = (pd.Timestamp(now) - df["date"]).dt.total_seconds() / 86400
    df["fresh"] = df["age_days"] < FRESH_HOURS / 24

    # базовая линия: медиана предыдущих BASELINE_WINDOW «зрелых» постов
    mature = df[~df["fresh"] & ~df["is_pinned"]]
    global_median = mature["views"].median() if len(mature) else df["views"].median()
    baseline = (
        df["views"].where(~df["fresh"] & ~df["is_pinned"])
        .shift(1)
        .rolling(BASELINE_WINDOW, min_periods=5)
        .median()
    )
    df["baseline"] = baseline.fillna(global_median)
    df["virality"] = np.where(df["baseline"] > 0, df["views"] / df["baseline"], np.nan)
    df["analyzable"] = ~df["fresh"] & ~df["is_pinned"] & df["virality"].notna()
    df["channel_name"] = channel.display_name
    df["role"] = channel.role
    return df.reset_index(drop=True)


def bucket_stats(df: pd.DataFrame, col: str, order: Optional[list] = None,
                 labels: Optional[dict] = None) -> list[dict]:
    """Медианная виральность по группам + тест Манна–Уитни «группа против остальных»."""
    data = df[df["analyzable"]]
    out = []
    keys = order if order is not None else sorted(data[col].dropna().unique().tolist())
    for key in keys:
        grp = data[data[col] == key]["virality"]
        rest = data[data[col] != key]["virality"]
        if len(grp) == 0:
            continue
        p = None
        if len(grp) >= 5 and len(rest) >= 5:
            try:
                p = float(mannwhitneyu(grp, rest, alternative="two-sided").pvalue)
            except ValueError:
                p = None
        out.append({
            "key": labels.get(key, key) if labels else key,
            "raw_key": key if not isinstance(key, (np.integer,)) else int(key),
            "n": int(len(grp)),
            "share": round(len(grp) / max(1, len(data)), 3),
            "median_virality": round(float(grp.median()), 2),
            "median_views": int(data[data[col] == key]["views"].median()),
            "p_value": round(p, 4) if p is not None else None,
            "reliable": bool(len(grp) >= MIN_BUCKET),
        })
    return out


TREND_MIN_AGE_DAYS = 7   # в Telegram посты добирают просмотры неделями — для тренда берём «зрелые»


def growth_trend(df: pd.DataFrame) -> dict:
    data = df[(df["age_days"] >= TREND_MIN_AGE_DAYS) & ~df["is_pinned"] & (df["views"] > 0)]
    if len(data) < 20:
        return {"monthly_change": None, "weekly": []}
    t = (data["date"] - data["date"].min()).dt.total_seconds() / 86400
    # оценка Тейла–Сена устойчива к единичным «залетевшим» постам, в отличие от МНК
    slope, _, lo, hi = theilslopes(np.log(data["views"]), t, alpha=0.90)
    weekly = (
        data.set_index("date")["views"].resample("W").median().dropna()
    )
    return {
        "monthly_change": round(float(np.exp(slope * 30) - 1), 3),
        "monthly_change_ci": [round(float(np.exp(lo * 30) - 1), 3), round(float(np.exp(hi * 30) - 1), 3)],
        "significant": bool(lo > 0 or hi < 0),
        "weekly": [{"week": d.strftime("%Y-%m-%d"), "median_views": int(v)} for d, v in weekly.items()],
    }


def posting_rhythm(df: pd.DataFrame) -> dict:
    if len(df) < 2:
        return {"posts_per_week": None, "median_gap_hours": None, "max_gap_days": None}
    span_days = max(1.0, (df["date"].max() - df["date"].min()).total_seconds() / 86400)
    gaps = df["date"].sort_values().diff().dropna().dt.total_seconds() / 3600
    return {
        "posts_per_week": round(len(df) / span_days * 7, 1),
        "median_gap_hours": round(float(gaps.median()), 1),
        "max_gap_days": round(float(gaps.max() / 24), 1),
        "period_days": round(span_days),
    }


def top_posts(df: pd.DataFrame, n: int = 5, worst: bool = False) -> list[dict]:
    data = df[df["analyzable"]].sort_values("virality", ascending=worst).head(n)
    return [
        {
            "post_id": r.post_id,
            "date": r.local.strftime("%d.%m.%Y %H:%M"),
            "views": int(r.views),
            "virality": round(float(r.virality), 2),
            "media_type": MEDIA_LABELS_RU.get(r.media_type, r.media_type),
            "snippet": (r.text[:220] + "…") if len(r.text) > 220 else r.text,
            "url": r.url,
        }
        for r in data.itertuples()
    ]


def compute_metrics(channel: Channel, now: Optional[datetime] = None) -> dict:
    df = prepare_frame(channel, now=now)
    if df.empty:
        return {"channel": channel.summary(), "empty": True}
    mature = df[~df["fresh"] & ~df["is_pinned"]]
    median_views = float(mature["views"].median()) if len(mature) else float(df["views"].median())
    subs = channel.subscribers
    has_engagement = bool(df["engagement"].sum() > 0)
    return {
        "channel": channel.summary(),
        "empty": False,
        "n_posts": int(len(df)),
        "n_analyzable": int(df["analyzable"].sum()),
        "median_views": int(median_views),
        "err": round(median_views / subs, 4) if subs else None,
        "median_er": round(float(mature["er"].median()), 4) if has_engagement and len(mature) else None,
        "rhythm": posting_rhythm(df),
        "trend": growth_trend(df),
        "by_weekday": bucket_stats(df, "weekday", order=list(range(7)),
                                   labels=dict(enumerate(WEEKDAYS_RU))),
        "by_slot": bucket_stats(df, "slot", order=[s[2] for s in SLOTS]),
        "by_media": bucket_stats(df, "media_type", labels=MEDIA_LABELS_RU),
        "by_length": bucket_stats(df, "length_bucket", order=[b[2] for b in LENGTH_BUCKETS]),
        "top_posts": top_posts(df, 5),
        "flop_posts": top_posts(df, 5, worst=True),
    }
