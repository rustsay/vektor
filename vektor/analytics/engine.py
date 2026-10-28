"""Оркестровка аналитики: метрики автора и конкурентов, темы, бенчмарк, факты."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

import pandas as pd

from ..models import Channel, MEDIA_LABELS_RU
from .facts import build_facts
from .metrics import bucket_stats, compute_metrics, prepare_frame
from .topics import fit_topics, topic_performance


def _benchmark_row(m: dict) -> dict:
    ch = m["channel"]
    if m.get("empty"):
        return {"title": ch["title"], "role": ch["role"], "empty": True}
    rel = [b for b in m["by_media"] if b["reliable"]]
    top_fmt = max(rel, key=lambda b: b["median_virality"])["key"] if rel else None
    return {
        "title": ch["title"],
        "platform": ch["platform"],
        "role": ch["role"],
        "subscribers": ch["subscribers"],
        "posts": m["n_posts"],
        "posts_per_week": m["rhythm"]["posts_per_week"],
        "median_views": m["median_views"],
        "err": m["err"],
        "median_er": m["median_er"],
        "monthly_change": m["trend"]["monthly_change"],
        "top_format": top_fmt,
    }


def analyze(author: Channel, competitors: list[Channel], now: Optional[datetime] = None) -> dict:
    author.role = "author"
    for c in competitors:
        c.role = "competitor"

    a_metrics = compute_metrics(author, now=now)
    c_metrics = [compute_metrics(c, now=now) for c in competitors]

    frames = [prepare_frame(author, now=now)] + [prepare_frame(c, now=now) for c in competitors]
    frames = [f for f in frames if not f.empty]
    comp_frames = [f for f in frames if (f["role"] == "competitor").any()]
    comp_media = bucket_stats(pd.concat(comp_frames, ignore_index=True), "media_type",
                              labels=MEDIA_LABELS_RU) if comp_frames else []

    topics = fit_topics(frames)
    perf = topic_performance(topics)

    facts = build_facts(a_metrics, c_metrics, comp_media, perf) if not a_metrics.get("empty") else []

    return {
        "author": a_metrics,
        "competitors": c_metrics,
        "competitor_media": comp_media,
        "benchmark": [_benchmark_row(a_metrics)] + [_benchmark_row(m) for m in c_metrics],
        "topics": {"ok": topics.get("ok"), "reason": topics.get("reason"),
                   "list": topics.get("topics", []), "n_docs": topics.get("n_docs")},
        "topic_performance": {k: v for k, v in perf.items()},
        "facts": facts,
    }
