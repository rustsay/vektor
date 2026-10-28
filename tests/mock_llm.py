"""Тестовая заглушка OpenAI-совместимой LLM (только для автотестов!).

Отвечает по шаблону и намеренно «галлюцинирует»: ссылается на несуществующий
факт F99 и выдаёт рекомендацию без ссылок, чтобы проверить, что конвейер
такие идеи отбрасывает. Не использовать для реальных отчётов.
"""
from __future__ import annotations

import json
import re

from flask import Flask, request


def _facts(user: str) -> list[tuple[str, str]]:
    return re.findall(r"^(F\d+) \[([^;]+);", user, flags=re.M)


def agent_reply(system: str, user: str) -> dict:
    facts = _facts(user)
    ids = [f for f, _ in facts]
    recs = []
    if "аналитик данных" in system:
        fmt = [f for f, c in facts if c == "формат"]
        tm = [f for f, c in facts if c == "время"]
        if fmt:
            recs.append({"title": "Сделать видео основным форматом", "action": "Выпускать видео 2 раза в неделю",
                         "rationale": "Видео заходит лучше", "facts": fmt[:2], "expected_effect": "рост охвата",
                         "priority": "высокий"})
        if tm:
            recs.append({"title": "Сдвинуть публикации на вечер", "action": "Публиковать в 18–21",
                         "rationale": "Вечерний слот лучше", "facts": tm[:1], "expected_effect": "охват первых суток",
                         "priority": "средний"})
        recs.append({"title": "Повысить частоту публикаций", "action": "Публиковать ежедневно",
                     "rationale": "Конкуренты публикуют чаще", "facts": ids[:1], "expected_effect": "больше касаний",
                     "priority": "низкий"})
    elif "контент-стратег" in system:
        strong = [f for f, c in facts if c == "сильная тема"]
        recs.append({"title": "Рубрика про нейросети", "action": "Еженедельная рубрика",
                     "rationale": "Сильная тема", "facts": strong[:1] or ids[:1], "expected_effect": "виральность",
                     "priority": "высокий"})
        recs.append({"title": "Чаще использовать эмодзи", "action": "Эмодзи в каждом заголовке",
                     "rationale": "Так делают все", "facts": [], "expected_effect": "?", "priority": "низкий"})
    else:
        gap = [f for f, c in facts if c == "ниша"]
        recs.append({"title": "Занять нишу собеседований", "action": "Серия разборов собеседований",
                     "rationale": "Ниша свободна", "facts": gap[:1] or ids[:1], "expected_effect": "новая аудитория",
                     "priority": "высокий"})
        recs.append({"title": "Запустить TikTok", "action": "Переносить видео в TikTok",
                     "rationale": "Там растут конкуренты", "facts": ["F99"], "expected_effect": "охват",
                     "priority": "средний"})
    return {"recommendations": recs}


def critic_reply(user: str) -> dict:
    recs = json.loads(user.split("Рекомендации агентов:\n", 1)[1])
    verdicts, directions, used = [], [], set()
    for r in recs:
        if "частот" in r["title"].lower():
            verdicts.append({"rec_id": r["rec_id"], "verdict": "reject",
                             "reason": "опирается только на обзорный факт, не доказывает связь частоты и охвата"})
            continue
        verdicts.append({"rec_id": r["rec_id"], "verdict": "accept", "reason": "подтверждено"})
        if r["agent"] not in used and len(directions) < 3:
            used.add(r["agent"])
            directions.append({"title": r["title"], "why": r["rationale"], "facts": r["facts"],
                               "actions": [r["action"]], "kpi": "медианная виральность"})
    directions.append({"title": "Выдуманное направление", "why": "—", "facts": ["F77"], "actions": [], "kpi": ""})
    return {
        "verdicts": verdicts,
        "directions": directions,
        "content_plan": [{"day": d, "topic": "тема", "format": "видео", "time": "19:00", "hook": "хук"} for d in (1, 3, 5)],
        "experiments": [{"hypothesis": "h", "how": "A/B", "metric": "виральность", "duration": "2 недели"}],
        "summary": "Тестовое резюме.",
        "avoid": [],
    }


def reply(messages: list[dict]) -> str:
    system = messages[0]["content"]
    user = messages[-1]["content"]
    data = critic_reply(user) if "агент-критик" in system else agent_reply(system, user)
    # оборачиваем в ```json, как часто делают реальные модели
    return "Вот ответ:\n```json\n" + json.dumps(data, ensure_ascii=False) + "\n```"


class FakeLLM:
    """Та же логика без HTTP — для быстрых модульных тестов."""
    name = "fake-llm"

    def __init__(self, fail: bool = False):
        self.fail = fail
        self.calls = 0

    def chat(self, messages, temperature=0.5):
        self.calls += 1
        if self.fail:
            raise RuntimeError("модель недоступна")
        return reply(messages)


def make_app() -> Flask:
    app = Flask(__name__)

    @app.post("/v1/chat/completions")
    def completions():
        body = request.get_json()
        return {"choices": [{"message": {"role": "assistant", "content": reply(body["messages"])}}]}

    @app.post("/api/chat")
    def ollama_chat():
        body = request.get_json()
        assert body["options"]["num_ctx"] >= 8192
        # формат json у Ollama — чистый JSON без обёртки
        text = reply(body["messages"])
        clean = text.split("```json\n", 1)[1].rsplit("\n```", 1)[0]
        return {"message": {"role": "assistant", "content": clean}, "done": True}

    return app


if __name__ == "__main__":
    make_app().run(port=8099)
