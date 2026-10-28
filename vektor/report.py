"""Отчёт в Markdown — для скачивания и отправки автору."""
from __future__ import annotations

from datetime import datetime


def _pct(v):
    return "—" if v is None else f"{v * 100:.1f}%".replace(".", ",")


def _num(v):
    return "—" if v is None else f"{int(v):,}".replace(",", " ")


def mode_text(st: dict) -> str:
    model = st.get("model")
    if st["mode"] == "llm":
        return f"LLM ({model})"
    if st["mode"] == "mixed":
        return f"смешанный: LLM ({model}) отработала на {st.get('llm_steps_ok')} из 4 шагов, остальные — на правилах"
    if model:
        return f"офлайн: LLM ({model}) не ответила, использованы правила"
    return "офлайн (правила, без LLM)"


def render_markdown(result: dict) -> str:
    an, st = result["analysis"], result["strategy"]
    author = an["author"]["channel"]
    fmap = {f["id"]: f for f in an["facts"]}
    L: list[str] = []
    L.append(f"# «Вектор»: стратегия для канала «{author['title']}»")
    L.append("")
    mode = mode_text(st)
    L.append(f"_Сформировано {datetime.now().strftime('%d.%m.%Y %H:%M')} · режим агентов: {mode} · "
             f"источник данных: {author.get('source_note', '')}_")
    L.append("")
    for w in result.get("warnings") or []:
        L += [f"> ⚠ {w}", ""]
    for e in st.get("llm_errors") or []:
        L += [f"> ⚠ {e['step']}: LLM не сработала — {e['error']}", ""]
    if st.get("summary"):
        L += ["## Резюме", "", st["summary"], ""]

    L += ["## 3 направления развития", ""]
    for i, d in enumerate(st["directions"], 1):
        L.append(f"### {i}. {d.get('title', '')}")
        L.append("")
        L.append(d.get("why", ""))
        L.append("")
        for a in d.get("actions", []) or []:
            L.append(f"- {a}")
        if d.get("kpi"):
            L.append(f"- **Как измерить:** {d['kpi']}")
        L.append(f"- **Основано на фактах:** {', '.join(d.get('facts', []))}")
        L.append("")

    if st["content_plan"]:
        L += ["## Контент-план на 14 дней", "", "| День | Тема | Формат | Время | Идея |", "|---|---|---|---|---|"]
        for p in st["content_plan"]:
            L.append(f"| {p.get('day', '')} | {p.get('topic', '')} | {p.get('format', '')} | {p.get('time', '')} | {p.get('hook', '')} |")
        L.append("")

    if st["experiments"]:
        L += ["## Эксперименты", ""]
        for e in st["experiments"]:
            L.append(f"- **Гипотеза:** {e.get('hypothesis', '')}  \n  **Как проверить:** {e.get('how', '')} · "
                     f"**Метрика:** {e.get('metric', '')} · **Срок:** {e.get('duration', '')}")
        L.append("")

    if st["avoid"]:
        L += ["## Чего избегать", ""] + [f"- {a}" for a in st["avoid"]] + [""]

    L += ["## Сравнение с конкурентами", "",
          "| Канал | Подписчики | Постов/нед | Медианный охват | ERR | ER | Тренд/мес | Лучший формат |",
          "|---|---|---|---|---|---|---|---|"]
    for b in an["benchmark"]:
        if b.get("empty"):
            continue
        name = f"**{b['title']}**" if b["role"] == "author" else b["title"]
        ppw = str(b["posts_per_week"]).replace(".", ",") if b["posts_per_week"] is not None else "—"
        L.append(f"| {name} | {_num(b['subscribers'])} | {ppw} | {_num(b['median_views'])} | "
                 f"{_pct(b['err'])} | {_pct(b['median_er'])} | {_pct(b['monthly_change'])} | {b['top_format'] or '—'} |")
    L.append("")

    L += ["## Фактическая база", "", "Каждый вывод выше ссылается на эти факты.", ""]
    for f in an["facts"]:
        L.append(f"- **{f['id']}** ({f['category']}, уверенность: {f['confidence']}): {f['text']}")
    L.append("")

    if st["rejected"]:
        L += ["## Отклонённые идеи", "", "Идеи агентов, которые не прошли проверку:", ""]
        for r in st["rejected"]:
            L.append(f"- ~~{r.get('title', '')}~~ ({r.get('agent', '')}) — {r.get('reject_reason', '')} [{r.get('rejected_by', '')}]")
        L.append("")
    L.append("---")
    L.append("Методика: индекс виральности = охват поста / медиана охвата 15 предыдущих постов канала; "
             "посты моложе 72 часов исключены; значимость различий — тест Манна–Уитни; "
             "темы — TF-IDF + NMF по стеммированному тексту.")
    return "\n".join(L)
