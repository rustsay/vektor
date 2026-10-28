"""Мультиагентный конвейер.

  факты ──┬─► Аналитик метрик (t=0.3) ──┐
          ├─► Контент-стратег  (t=0.7) ──┼─► проверка ссылок на факты ─► Агент-критик ─► стратегия
          └─► Конкурентный аналитик (t=1.0) ┘      (детерминированная)       (t=0.2)

Агенты работают параллельно с разной «температурой» (разная степень смелости
идей), критик принимает или отклоняет каждую идею. Без LLM работает
офлайн-режим: те же роли, но на правилах.
"""
from __future__ import annotations

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Optional

from .llm import ChatModel, chat_json
from .prompts import AGENTS, AGENT_SCHEMA, COMMON_RULES, CRITIC_SYSTEM, facts_block

Progress = Callable[[str], None]
log = logging.getLogger("vektor.agents")

PRIORITY_RANK = {"высокий": 0, "средний": 1, "низкий": 2}
CONF_RANK = {"высокая": 0, "средняя": 1, "низкая": 2}


# ---------------------------------------------------------------- проверка ссылок

def validate_recommendations(recs: list[dict], fact_ids: set[str]) -> tuple[list[dict], list[dict]]:
    """Детерминированный фильтр: ссылки на факты обязательны и должны существовать."""
    valid, rejected = [], []
    for r in recs:
        cited = [f for f in (r.get("facts") or []) if isinstance(f, str)]
        good = [f for f in cited if f in fact_ids]
        bad = [f for f in cited if f not in fact_ids]
        if not good:
            reason = ("ссылается на несуществующие факты: " + ", ".join(bad)) if bad else "нет ссылок на факты"
            rejected.append({**r, "reject_reason": reason, "rejected_by": "проверка ссылок"})
            continue
        r = {**r, "facts": good}
        if bad:
            r["dropped_facts"] = bad
        valid.append(r)
    return valid, rejected


def _facts_for(agent_key: str, facts: list[dict]) -> list[dict]:
    cats = set(AGENTS[agent_key]["categories"])
    return [f for f in facts if f["category"] in cats]


# ---------------------------------------------------------------- офлайн-агенты (правила)

def _slot_time(slot: str) -> str:
    start = int(slot.split("–")[0])
    return f"{start + 1:02d}:00"


def rule_agent(agent_key: str, facts: list[dict]) -> list[dict]:
    recs: list[dict] = []
    by_cat: dict[str, list[dict]] = {}
    for f in facts:
        by_cat.setdefault(f["category"], []).append(f)

    def add(title, action, rationale, fids, effect, priority):
        recs.append({"title": title, "action": action, "rationale": rationale, "facts": fids,
                     "expected_effect": effect, "priority": priority})

    if agent_key == "metrics":
        for f in by_cat.get("формат", []):
            d = f["data"]
            if "most" in d:
                b = d["best"]
                add(f"Больше постов в формате «{b['key']}»",
                    f"Поднять долю формата «{b['key']}» примерно до 30% публикаций.",
                    f["text"], [f["id"]], "Рост медианного охвата; сравнить через 4 недели.",
                    "высокий" if f["confidence"] != "низкая" else "средний")
                break
        for f in by_cat.get("время", []):
            b, w = f["data"]["best"], f["data"]["worst"]
            add(f"Публиковать в {b['key']} (МСК)",
                f"Перенести основные публикации в слот {b['key']}, избегать слота {w['key']}.",
                f["text"], [f["id"]], "Рост охвата первых суток.",
                "средний" if f["confidence"] != "низкая" else "низкий")
        for f in by_cat.get("ритм", []):
            d = f["data"]
            comp = d.get("competitors_ppw") or []
            if comp:
                med = sorted(comp)[len(comp) // 2]
                if med > d["posts_per_week"] * 1.3:
                    target = round((d["posts_per_week"] + med) / 2)
                    add("Увеличить частоту публикаций",
                        f"Перейти на {target} публикаций в неделю за счёт коротких форматов.",
                        f["text"], [f["id"]], "Больше касаний с аудиторией без потери охвата.", "средний")
        for f in by_cat.get("динамика", []):
            if f["data"].get("monthly_change", 0) < -0.03 and f["confidence"] == "высокая":
                add("Остановить падение охватов", "Пересобрать рубрикатор вокруг тем и форматов с виральностью выше 1×.",
                    f["text"], [f["id"]], "Стабилизация тренда.", "высокий")
    elif agent_key == "content":
        for f in by_cat.get("сильная тема", [])[:2]:
            add(f"Рубрика по теме «{f['data']['topic']}»",
                f"Сделать регулярную рубрику (1–2 раза в неделю) по теме «{f['data']['topic']}».",
                f["text"], [f["id"]], "Рост доли виральных постов.", "высокий")
        for f in by_cat.get("слабая тема", [])[:1]:
            add(f"Переупаковать тему «{f['data']['topic']}»",
                "Сократить частоту и сменить подачу: личная история или разбор кейса вместо общих советов.",
                f["text"], [f["id"]], "Виральность темы ≥ 1×.", "средний")
        tops = by_cat.get("лучший пост", [])
        if tops:
            add("Продолжения лучших постов",
                "Сделать продолжение или «часть 2» для трёх самых успешных постов.",
                tops[0]["text"], [t["id"] for t in tops], "Повторение успешного паттерна.", "средний")
    elif agent_key == "competitors":
        for f in by_cat.get("ниша", [])[:2]:
            add(f"Занять нишу «{f['data']['topic']}»",
                f"Запустить серию постов по теме «{f['data']['topic']}» со своим углом, а не копией конкурентов.",
                f["text"], [f["id"]], "Привлечение аудитории, которую уже собрали конкуренты.", "высокий")
        for f in by_cat.get("формат у конкурентов", []):
            add(f"Перенять формат «{f['data']['best']['key']}» у конкурентов",
                f"Протестировать формат «{f['data']['best']['key']}» в своей подаче.",
                f["text"], [f["id"]], "Проверка формата на своей аудитории.", "средний")
    return recs


def rule_critic(candidates: list[dict], facts: list[dict], author_title: str) -> dict:
    fmap = {f["id"]: f for f in facts}

    def conf(r):
        return min(CONF_RANK.get(fmap[f]["confidence"], 2) for f in r["facts"])

    ranked = sorted(candidates, key=lambda r: (PRIORITY_RANK.get(r.get("priority"), 1), conf(r)))
    verdicts, directions, used_titles = [], [], set()
    for r in ranked:
        low = conf(r) == 2 and r.get("priority") == "высокий"
        if low:
            verdicts.append({"rec_id": r["rec_id"], "verdict": "reject",
                             "reason": "сильный вывод на факте с низкой уверенностью — перенесено в эксперименты"})
            continue
        verdicts.append({"rec_id": r["rec_id"], "verdict": "accept", "reason": "подтверждается фактами"})

    # по одному направлению от каждого агента — так стратегия получается разносторонней
    accepted_ids = {v["rec_id"] for v in verdicts if v["verdict"] == "accept"}
    pool = [r for r in ranked if r["rec_id"] in accepted_ids]
    used_agents: set[str] = set()
    for pass_no in range(2):
        for r in pool:
            if len(directions) >= 3:
                break
            if r["title"] in used_titles or (pass_no == 0 and r.get("agent") in used_agents):
                continue
            used_titles.add(r["title"])
            used_agents.add(r.get("agent"))
            directions.append({"title": r["title"], "why": r["rationale"], "facts": r["facts"],
                               "actions": [r["action"]], "kpi": r.get("expected_effect", "")})

    # контент-план: лучшее время × лучший формат × сильные темы и ниши
    slot = next((f["data"]["best"]["key"] for f in facts if f["category"] == "время"), "18–21")
    fmt = next((f["data"]["best"]["key"] for f in facts if f["category"] == "формат"), "текст")
    topics = ([f["data"]["topic"] for f in facts if f["category"] == "сильная тема"] +
              [f["data"]["topic"] for f in facts if f["category"] == "ниша"]) or ["основная тема канала"]
    plan = []
    for i, day in enumerate([1, 3, 5, 7, 9, 11, 13]):
        topic = topics[i % len(topics)]
        plan.append({"day": day, "topic": topic, "format": fmt if i % 2 == 0 else "текст",
                     "time": _slot_time(slot), "hook": f"Личный опыт или разбор кейса по теме «{topic}»"})

    experiments = []
    for f in facts:
        if f["confidence"] == "низкая" and f["category"] in ("время", "день недели", "длина", "формат"):
            experiments.append({"hypothesis": f["text"], "how": "Две недели чередовать варианты A/B, остальное не менять.",
                                "metric": "медианная виральность", "duration": "2 недели"})
    summary = (f"Для канала «{author_title}» ключевые точки роста: "
               + "; ".join(d["title"].lower() for d in directions) + ".") if directions else \
        "Недостаточно данных для уверенных рекомендаций."
    avoid = [f"{f['text'][:140]}… ({f['id']})" for f in facts if f["category"] == "слабая тема"][:2]
    return {"verdicts": verdicts, "directions": directions, "content_plan": plan,
            "experiments": experiments[:3], "summary": summary, "avoid": avoid}


# ---------------------------------------------------------------- LLM-агенты

def llm_agent(llm: ChatModel, agent_key: str, facts: list[dict]) -> list[dict]:
    spec = AGENTS[agent_key]
    messages = [
        {"role": "system", "content": f"{spec['role']}\n\n{COMMON_RULES}\n\n{AGENT_SCHEMA}"},
        {"role": "user", "content": "Фактическая база:\n" + facts_block(facts)},
    ]
    data = chat_json(llm, messages, temperature=spec["temperature"])
    recs = data.get("recommendations", [])
    return [r for r in recs if isinstance(r, dict) and r.get("title")]


def llm_critic(llm: ChatModel, candidates: list[dict], facts: list[dict]) -> dict:
    recs_txt = json.dumps(
        [{k: r.get(k) for k in ("rec_id", "agent", "title", "action", "rationale", "facts", "priority")} for r in candidates],
        ensure_ascii=False, indent=1)
    messages = [
        {"role": "system", "content": CRITIC_SYSTEM},
        {"role": "user", "content": "Фактическая база:\n" + facts_block(facts) + "\n\nРекомендации агентов:\n" + recs_txt},
    ]
    return chat_json(llm, messages, temperature=0.2)


# ---------------------------------------------------------------- конвейер

def run_strategy(analysis: dict, llm: Optional[ChatModel] = None,
                 progress: Optional[Progress] = None) -> dict:
    say = progress or (lambda msg: None)
    facts = analysis["facts"]
    fact_ids = {f["id"] for f in facts}
    mode = "llm" if llm is not None else "offline"
    trace: list[dict] = []

    def run_agent(key: str) -> dict:
        t0 = time.time()
        subset = _facts_for(key, facts)
        err = None
        try:
            recs = llm_agent(llm, key, subset) if llm else rule_agent(key, subset)
        except Exception as e:     # агент упал — не роняем весь конвейер
            err = str(e)
            log.warning("%s: LLM не сработала (%s) — использую правила", AGENTS[key]["name"], err)
            recs = rule_agent(key, subset)
        return {"key": key, "name": AGENTS[key]["name"], "temperature": AGENTS[key]["temperature"],
                "facts_seen": len(subset), "recommendations": recs, "seconds": round(time.time() - t0, 2),
                "error": err, "fallback": err is not None}

    say("Агенты анализируют факты параллельно…")
    workers = getattr(llm, "parallel", 3) if llm else 3
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        agents = list(ex.map(run_agent, ["metrics", "content", "competitors"]))
    for a in agents:
        trace.append({"step": a["name"], "seconds": a["seconds"], "items": len(a["recommendations"]),
                      "note": "ошибка LLM — использованы правила" if a["fallback"] else ""})

    candidates = []
    for a in agents:
        for r in a["recommendations"]:
            r["rec_id"] = f"R{len(candidates) + 1}"
            candidates.append({**r, "agent": a["name"]})

    say("Проверяю ссылки на факты…")
    valid, pre_rejected = validate_recommendations(candidates, fact_ids)
    trace.append({"step": "Проверка ссылок на факты", "seconds": 0, "items": len(valid),
                  "note": f"отброшено {len(pre_rejected)}"})

    say("Агент-критик проверяет рекомендации и собирает стратегию…")
    t0 = time.time()
    critic_error = None
    try:
        verdict = llm_critic(llm, valid, facts) if llm and valid else rule_critic(valid, facts, analysis["author"]["channel"]["title"])
    except Exception as e:
        critic_error = str(e)
        log.warning("Агент-критик: LLM не сработала (%s) — использую правила", critic_error)
        verdict = rule_critic(valid, facts, analysis["author"]["channel"]["title"])

    # повторная детерминированная проверка уже ответа критика
    directions, bad_dirs = [], []
    for d in verdict.get("directions", []) or []:
        good = [f for f in (d.get("facts") or []) if f in fact_ids]
        if good:
            directions.append({**d, "facts": good})
        else:
            bad_dirs.append({**d, "reject_reason": "направление критика без валидных ссылок на факты",
                             "rejected_by": "проверка ссылок"})
    trace.append({"step": "Агент-критик", "seconds": round(time.time() - t0, 2), "items": len(directions),
                  "note": "ошибка LLM — использованы правила" if critic_error else ""})

    vmap = {v.get("rec_id"): v for v in verdict.get("verdicts", []) or []}
    rejected = pre_rejected + [
        {**r, "reject_reason": vmap[r["rec_id"]].get("reason", ""), "rejected_by": "агент-критик"}
        for r in valid if vmap.get(r["rec_id"], {}).get("verdict") == "reject"
    ] + bad_dirs
    accepted = [r for r in valid if vmap.get(r["rec_id"], {}).get("verdict", "accept") != "reject"]

    # честный итоговый режим: сколько шагов реально выполнила LLM
    llm_errors = [{"step": a["name"], "error": a["error"]} for a in agents if a["error"]]
    if critic_error:
        llm_errors.append({"step": "Агент-критик", "error": critic_error})
    if llm is not None:
        ok_steps = 4 - len(llm_errors)
        mode = "llm" if ok_steps == 4 else "offline" if ok_steps == 0 else "mixed"
    else:
        ok_steps = 0

    return {
        "mode": mode,
        "llm_steps_ok": ok_steps,
        "llm_errors": llm_errors,
        "model": getattr(llm, "name", None),
        "agents": agents,
        "accepted": accepted,
        "rejected": rejected,
        "directions": directions[:3],
        "content_plan": verdict.get("content_plan", []) or [],
        "experiments": verdict.get("experiments", []) or [],
        "summary": verdict.get("summary", ""),
        "avoid": verdict.get("avoid", []) or [],
        "trace": trace,
        "critic_error": critic_error,
    }
