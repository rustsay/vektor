"""Фактическая база: каждый вывод аналитики превращается в пронумерованный факт.

Агенты обязаны ссылаться на факты по ID (F1, F2, …). Агент-критик
отбрасывает рекомендации без ссылок или со ссылками на несуществующие
факты — это главный механизм защиты от «галлюцинаций» LLM.
"""
from __future__ import annotations

from typing import Optional


def _confidence(n: int, p: Optional[float]) -> str:
    if p is not None and n >= 8 and p < 0.05:
        return "высокая"
    if n >= 5 and (p is None or p < 0.2):
        return "средняя"
    return "низкая"


def _x(v: float) -> str:
    return f"{v:.2f}×".replace(".", ",")


def _pct(v: float) -> str:
    return f"{v * 100:.1f}%".replace(".", ",")


def _int(v: float) -> str:
    return f"{int(round(v)):,}".replace(",", " ")


def _quote(snippet: str) -> str:
    text = " ".join((snippet or "").split())
    if not text:
        return "пост без текста"
    if len(text) > 160:
        text = text[:160].rsplit(" ", 1)[0] + "…"
    return f"«{text}»"


class FactBook:
    def __init__(self):
        self.facts: list[dict] = []

    def add(self, category: str, text: str, confidence: str = "высокая", **data) -> str:
        fid = f"F{len(self.facts) + 1}"
        self.facts.append({"id": fid, "category": category, "text": text,
                           "confidence": confidence, "data": data})
        return fid


def _best_worst(buckets: list[dict]) -> tuple[Optional[dict], Optional[dict]]:
    rel = [b for b in buckets if b["reliable"]]
    if len(rel) < 2:
        return None, None
    best = max(rel, key=lambda b: b["median_virality"])
    worst = min(rel, key=lambda b: b["median_virality"])
    if best is worst:
        return None, None
    return best, worst


def build_facts(author: dict, competitors: list[dict], comp_media: list[dict],
                topic_perf: dict) -> list[dict]:
    fb = FactBook()
    name = author["channel"]["title"]

    # 1. Общая картина
    rh = author["rhythm"]
    overview = (f"Канал «{name}»: проанализировано {author['n_posts']} постов за {rh.get('period_days', '?')} дн.; "
                f"медианный охват поста — {_int(author['median_views'])}")
    if author.get("err") is not None:
        overview += f", ERR (охват/подписчики) — {_pct(author['err'])}"
    if author.get("median_er") is not None:
        overview += f", ER (реакции/охват) — {_pct(author['median_er'])}"
    fb.add("обзор", overview + ".", median_views=author["median_views"], err=author.get("err"))

    # 2. Динамика
    tr = author["trend"]
    mc = tr.get("monthly_change")
    if mc is not None:
        if not tr.get("significant"):
            direction = "статистически значимо не меняется"
        else:
            direction = "растёт" if mc > 0 else "падает"
        lo, hi = tr.get("monthly_change_ci", [None, None])
        ci = f"; 90% интервал: от {_pct(lo)} до {_pct(hi)}" if lo is not None else ""
        caveat = ""
        if author["channel"]["platform"] == "telegram":
            caveat = (" Учтены посты старше 7 дней; в Telegram старые посты продолжают медленно набирать просмотры, "
                      "поэтому реальный тренд может быть немного лучше оценки.")
        fb.add("динамика", f"Охват постов {direction}: оценка тренда {'+' if mc >= 0 else ''}{_pct(mc)} в месяц"
                           f"{ci} (робастная регрессия Тейла–Сена).{caveat}",
               confidence="высокая" if tr.get("significant") else "средняя", monthly_change=mc)

    # 3. Ритм публикаций и сравнение с конкурентами
    comp_ppw = [c["rhythm"]["posts_per_week"] for c in competitors if not c.get("empty") and c["rhythm"].get("posts_per_week")]
    if rh.get("posts_per_week") is not None:
        t = f"Автор публикует {str(rh['posts_per_week']).replace('.', ',')} поста в неделю; самый долгий перерыв — {str(rh['max_gap_days']).replace('.', ',')} дн."
        if comp_ppw:
            med = sorted(comp_ppw)[len(comp_ppw) // 2]
            t += f" У конкурентов в медиане {str(med).replace('.', ',')} поста в неделю."
        fb.add("ритм", t, posts_per_week=rh["posts_per_week"], competitors_ppw=comp_ppw)

    # 4–7. Время, день недели, формат, длина
    for key, cat, what in [("by_slot", "время", "публикации в слоте {k} (МСК)"),
                           ("by_weekday", "день недели", "публикации в {k}"),
                           ("by_length", "длина", "посты длиной {k}"),
                           ("by_media", "формат", "посты формата «{k}»")]:
        best, worst = _best_worst(author[key])
        if not best:
            continue
        conf = _confidence(min(best["n"], worst["n"]), best.get("p_value"))
        fb.add(cat,
               f"Лучше всего заходят {what.format(k=best['key'])}: медианная виральность {_x(best['median_virality'])} "
               f"(n={best['n']}); хуже всего — {what.format(k=worst['key'])}: {_x(worst['median_virality'])} (n={worst['n']}).",
               confidence=conf, best=best, worst=worst)

    # формат: несоответствие «что работает» и «что публикуем»
    rel = [b for b in author["by_media"] if b["reliable"]]
    if len(rel) >= 2:
        best = max(rel, key=lambda b: b["median_virality"])
        most = max(rel, key=lambda b: b["share"])
        if best is not most and best["share"] < 0.35 and best["median_virality"] > most["median_virality"] * 1.15:
            fb.add("формат",
                   f"Формат «{best['key']}» даёт {_x(best['median_virality'])} к обычному охвату, но составляет лишь "
                   f"{_pct(best['share'])} постов; основной формат «{most['key']}» ({_pct(most['share'])} постов) — {_x(most['median_virality'])}.",
                   confidence=_confidence(best["n"], best.get("p_value")), best=best, most=most)

    # 8. Лучшие и худшие посты
    for p in author["top_posts"][:3]:
        fb.add("лучший пост", f"Пост от {p['date']} ({p['media_type']}) набрал {_x(p['virality'])} к обычному охвату: {_quote(p['snippet'])}",
               confidence="высокая", post=p)
    for p in author["flop_posts"][:2]:
        fb.add("слабый пост", f"Пост от {p['date']} ({p['media_type']}) набрал лишь {_x(p['virality'])} к обычному охвату: {_quote(p['snippet'])}",
               confidence="высокая", post=p)

    # 9. Темы
    if topic_perf.get("ok"):
        for row in topic_perf["strong"][:3]:
            au = row["author"]
            fb.add("сильная тема", f"Тема «{row['label']}» у автора заходит выше среднего: {_x(au['median_virality'])} "
                                    f"(n={au['n']}, {_pct(au['share'])} постов). Ключевые слова: {', '.join(row['keywords'][:5])}.",
                   confidence=_confidence(au["n"], None), topic=row["label"])
        for row in topic_perf["weak"][:2]:
            au = row["author"]
            fb.add("слабая тема", f"Тема «{row['label']}» у автора заходит хуже среднего: {_x(au['median_virality'])} "
                                   f"(n={au['n']}, {_pct(au['share'])} постов).",
                   confidence=_confidence(au["n"], None), topic=row["label"])
        for row in topic_perf["gaps"][:3]:
            co, au = row["competitors"], row["author"]
            ex = row.get("competitor_examples") or []
            ex_txt = f" Пример у конкурента: «{ex[0]['snippet'][:120]}» ({_x(ex[0]['virality'])})." if ex else ""
            fb.add("ниша", f"Тема «{row['label']}» — {_pct(co['share'])} постов конкурентов с виральностью "
                           f"{_x(co['median_virality'])}, а у автора всего {_pct(au['share'])} постов.{ex_txt}",
                   confidence=_confidence(co["n"], None), topic=row["label"])

    # 10. Бенчмарк
    errs = [(c["channel"]["title"], c["err"]) for c in competitors if not c.get("empty") and c.get("err")]
    if author.get("err") and errs:
        better = [n for n, e in errs if e > author["err"]]
        med = sorted(e for _, e in errs)[len(errs) // 2]
        fb.add("конкуренты", f"ERR автора {_pct(author['err'])} против медианы конкурентов {_pct(med)}; "
                             f"выше, чем у автора, ERR у {len(better)} из {len(errs)} конкурентов.",
               author_err=author["err"], competitors=errs)
    best_c, _ = _best_worst(comp_media)
    if best_c:
        fb.add("формат у конкурентов", f"У конкурентов лучше всего заходит формат «{best_c['key']}»: "
                                        f"{_x(best_c['median_virality'])} (n={best_c['n']}).",
               confidence=_confidence(best_c["n"], best_c.get("p_value")), best=best_c)
    return fb.facts
