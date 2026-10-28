"""Генератор СИНТЕТИЧЕСКИХ демо-данных.

Нужен, чтобы показать работу системы без доступа к реальным каналам.
В данные специально «зашиты» закономерности (например, видео заходит
лучше текста, а тема собеседований есть у конкурентов, но не у автора),
и тест проверяет, что аналитика их находит. Все каналы вымышленные.
"""
from __future__ import annotations

import random
import zlib
from datetime import datetime, timedelta, timezone

import numpy as np

from .models import Channel, Post

TOPICS = {
    "interview": [
        "Разбираю вопросы с собеседования на позицию python-разработчика.",
        "Тестовое задание на собеседовании: как не провалить и получить оффер.",
        "Лайвкодинг на собеседовании пугает всех, но к нему можно подготовиться.",
        "HR спрашивает про ожидания по зарплате на собеседовании — что отвечать.",
        "Алгоритмические задачи на собеседовании: какие встречаются чаще всего.",
        "Получил оффер после пяти собеседований, делюсь выводами.",
        "Типичные ошибки кандидатов на техническом собеседовании.",
        "Как готовиться к собеседованию за неделю: план по дням.",
    ],
    "ai": [
        "Нейросети уже пишут код быстрее джуна, но им нужен контроль.",
        "Собрал агента на LLM, который автоматизирует рутину в почте.",
        "Промпты для ChatGPT, которые реально экономят время разработчика.",
        "Как нейросети меняют профессию программиста в этом году.",
        "Локальные языковые модели: запускаем LLM на своём ноутбуке.",
        "Автоматизация отчётов с помощью нейросети и пары строк кода.",
        "Сравнил три нейросети для генерации кода на реальной задаче.",
        "ИИ-агенты: что это такое и где они уже работают.",
    ],
    "python": [
        "Полезная библиотека python, о которой мало кто знает.",
        "Pandas: как ускорить обработку таблицы в десять раз.",
        "Пишем скрипт на python, который парсит сайт и сохраняет данные.",
        "Функции в python: разбираем аргументы по умолчанию и подводные камни.",
        "Генераторы и итераторы в python простыми словами.",
        "Как устроены декораторы в python и зачем они нужны в коде.",
        "Виртуальное окружение python: почему без него нельзя.",
        "Типизация в python: аннотации типов на практике.",
    ],
    "career": [
        "Как вырасти с junior до middle за год: личный опыт.",
        "Зарплаты в IT в этом году: что изменилось на рынке.",
        "Грейды в компаниях: чем middle отличается от senior на самом деле.",
        "Повышение зарплаты: как говорить с руководителем.",
        "Карьерный рост разработчика и роль менторства.",
        "Стоит ли менять работу ради зарплаты плюс тридцать процентов.",
    ],
    "freelance": [
        "Фриланс для разработчика: где искать первых заказчиков.",
        "Как оценить проект на фрилансе и не продешевить.",
        "Портфолио для фриланса: что показывать клиентам.",
        "Заказчик пропал после сдачи проекта — как защититься на фрилансе.",
        "Фриланс или офис: честное сравнение денег и свободы.",
    ],
    "motivation": [
        "Выгорание у программистов: как я его заметил и что сделал.",
        "Режим дня, который помогает учиться программированию после работы.",
        "Продуктивность без фанатизма: три привычки, которые работают.",
        "Как не бросить учёбу через месяц: про мотивацию и дисциплину.",
        "Отдых тоже часть работы: почему важно отключаться.",
        "Синдром самозванца у разработчиков и как с ним жить.",
    ],
    "memes": [
        "Пятничный мем: когда деплой в прод в шесть вечера.",
        "Баг, который нашёл тестировщик, а исправлять тебе.",
        "Мем недели: код работает, и никто не знает почему.",
        "Когда тимлид говорит, что задача на пять минут.",
        "Пятница, прод упал, а ты уже в такси.",
    ],
}

FILLER = [
    "Сохраняйте, чтобы не потерять.",
    "Пишите в комментариях, сталкивались ли вы с таким.",
    "Делюсь тем, что сам проверил на практике.",
]

LONG_EXTRA = (
    " Разберём подробно каждый пункт, с примерами и пояснениями. Сначала посмотрим на исходную ситуацию, "
    "потом на варианты решения, затем на то, что получилось в итоге и какие выводы можно сделать. "
    "В конце оставлю список материалов для самостоятельного изучения и несколько упражнений."
)

# (название, подписчики, базовый охват, постов/нед, распределение тем, эффекты)
PROFILES = {
    "author": {
        "username": "kod_i_kofe", "title": "Код и кофе (демо)", "subs": 4200, "base": 1500, "ppw": 6,
        "topics": {"python": .30, "motivation": .23, "ai": .15, "career": .15, "memes": .15, "interview": .02},
        "media": {"text": .58, "photo": .28, "video": .14},
        "media_fx": {"text": .9, "photo": 1.05, "video": 1.75},
        "topic_fx": {"ai": 1.45, "motivation": .78, "memes": 1.2, "python": 1.0, "career": 1.0, "interview": 1.3},
        "growth": .04,
    },
    "c1": {
        "username": "jun_na_starte", "title": "Джун на старте (демо)", "subs": 9800, "base": 3100, "ppw": 9,
        "topics": {"interview": .28, "career": .22, "python": .2, "memes": .15, "freelance": .15},
        "media": {"text": .45, "photo": .3, "video": .25},
        "media_fx": {"text": .9, "photo": 1.0, "video": 1.5},
        "topic_fx": {"interview": 1.5, "freelance": 1.25, "career": 1.0, "python": .9, "memes": 1.1},
        "growth": .07,
    },
    "c2": {
        "username": "senior_v_dele", "title": "Senior в деле (демо)", "subs": 15300, "base": 5200, "ppw": 7,
        "topics": {"career": .25, "interview": .2, "ai": .25, "python": .2, "motivation": .1},
        "media": {"text": .5, "photo": .25, "video": .25},
        "media_fx": {"text": .95, "photo": 1.0, "video": 1.4},
        "topic_fx": {"interview": 1.35, "ai": 1.3, "career": 1.05, "python": .9, "motivation": .8},
        "growth": .02,
    },
    "c3": {
        "username": "ii_bez_vody", "title": "ИИ без воды (демо)", "subs": 7100, "base": 2600, "ppw": 10,
        "topics": {"ai": .5, "freelance": .15, "python": .2, "memes": .15},
        "media": {"text": .5, "photo": .35, "video": .15},
        "media_fx": {"text": .95, "photo": 1.05, "video": 1.3},
        "topic_fx": {"ai": 1.2, "freelance": 1.3, "python": .85, "memes": 1.0},
        "growth": .05,
    },
}

SLOT_FX = {range(0, 6): .7, range(6, 9): .8, range(9, 12): 1.0, range(12, 15): 1.0,
           range(15, 18): 1.05, range(18, 21): 1.3, range(21, 24): 1.0}
WEEKDAY_FX = [1.0, 1.05, 1.0, 1.05, 1.0, .75, .85]

NOW = datetime(2026, 10, 27, 9, 0, tzinfo=timezone.utc)


def _pick(rng: random.Random, dist: dict) -> str:
    keys = list(dist)
    return rng.choices(keys, weights=[dist[k] for k in keys])[0]


def _slot_fx(hour: int) -> float:
    for r, fx in SLOT_FX.items():
        if hour in r:
            return fx
    return 1.0


def make_channel(key: str, days: int = 180, seed: int = 6) -> Channel:
    p = PROFILES[key]
    rng = random.Random(f"{seed}-{key}")
    nrng = np.random.default_rng(zlib.crc32(f"{seed}-{key}".encode()))
    n_posts = int(days / 7 * p["ppw"])
    start = NOW - timedelta(days=days)
    posts = []
    for i in range(n_posts):
        day = start + timedelta(days=rng.uniform(0, days))
        hour = rng.choices(range(24), weights=[1, 0, 0, 0, 0, 0, 2, 4, 5, 6, 6, 5, 6, 5, 5, 5, 6, 6, 7, 7, 6, 4, 3, 2])[0]
        local = day.replace(hour=hour, minute=rng.randint(0, 59))
        date = (local - timedelta(hours=3)).replace(tzinfo=timezone.utc)
        if date > NOW - timedelta(hours=2):
            continue
        topic = _pick(rng, p["topics"])
        media = _pick(rng, p["media"])
        sentences = rng.sample(TOPICS[topic], k=min(3, len(TOPICS[topic])))
        text = " ".join(sentences[: rng.randint(1, 3)]) + " " + rng.choice(FILLER)
        long = rng.random() < .25
        if long:
            pool = TOPICS[topic][:]
            rng.shuffle(pool)
            text += " " + " ".join(pool) + LONG_EXTRA + " " + " ".join(reversed(pool))
        t_months = (date - start).days / 30
        mean = (p["base"] * (1 + p["growth"]) ** t_months * p["media_fx"][media]
                * p["topic_fx"].get(topic, 1.0) * _slot_fx(local.hour) * WEEKDAY_FX[local.weekday()]
                * (.85 if long else 1.0))
        views = int(mean * nrng.lognormal(0, .28))
        age_h = (NOW - date).total_seconds() / 3600
        if age_h < 72:      # свежие посты ещё добирают охват
            views = int(views * min(1.0, .35 + age_h / 110))
        reactions = int(views * nrng.uniform(.02, .045))
        posts.append(Post(platform="demo", channel=p["username"], post_id=str(i + 1), date=date, text=text,
                          views=views, reactions=reactions, comments=int(reactions * nrng.uniform(.05, .2)),
                          reposts=int(reactions * nrng.uniform(.05, .15)), media_type=media,
                          url=""))
    posts.sort(key=lambda x: x.date)
    for i, post in enumerate(posts):
        post.post_id = str(i + 1)
    return Channel(platform="demo", channel=p["username"], title=p["title"], subscribers=p["subs"],
                   posts=posts, role="author" if key == "author" else "competitor",
                   source_note="синтетические демо-данные")


def demo_channels() -> tuple[Channel, list[Channel]]:
    return make_channel("author"), [make_channel(k) for k in ("c1", "c2", "c3")]
