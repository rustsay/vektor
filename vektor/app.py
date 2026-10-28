"""Веб-сервис «Вектор»: REST API + одностраничный интерфейс.

Запуск:  python -m vektor   →  http://localhost:8000
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import traceback
import uuid
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from flask import Flask, Response, abort, request, send_from_directory

from .agents.llm import llm_from_env
from .agents.pipeline import run_strategy
from .analytics.engine import analyze
from .connectors import CSVError, TelegramError, VKError, fetch_telegram, fetch_vk, load_csv
from .demo import NOW as DEMO_NOW, demo_channels
from .report import render_markdown

STATIC = Path(__file__).parent / "static"
MAX_JOBS = 50
MAX_COMPETITORS = 5
COMPETITOR_MAX_POSTS = 1000     # у новостных каналов период автора может вмещать сотни постов
SIZE_RATIO_WARN = 15            # во сколько раз канал должен отличаться, чтобы предупредить

app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024

_jobs: "OrderedDict[str, dict]" = OrderedDict()
_uploads: "OrderedDict[str, dict]" = OrderedDict()
_lock = threading.Lock()


def _default(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return None if np.isnan(o) else float(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, datetime):
        return o.isoformat()
    raise TypeError(f"не сериализуется: {type(o)}")


def _json(data, status: int = 200) -> Response:
    return Response(json.dumps(data, ensure_ascii=False, default=_default), status=status,
                    mimetype="application/json")


def _remember(store: OrderedDict, key: str, value: dict, limit: int):
    with _lock:
        store[key] = value
        while len(store) > limit:
            store.popitem(last=False)


# ------------------------------------------------------------------ сбор данных

def _collect(src: dict, role: str, max_posts: int, vk_token: str, since=None):
    platform = (src.get("platform") or "").lower()
    ident = (src.get("id") or "").strip()
    if platform == "telegram":
        return fetch_telegram(ident, max_posts=max_posts, role=role, since=since)
    if platform == "vk":
        return fetch_vk(ident, token=vk_token, max_posts=max_posts, role=role, since=since)
    if platform == "csv":
        up = _uploads.get(src.get("upload_id", ""))
        if not up:
            raise CSVError("Файл CSV не найден — загрузите его заново")
        return load_csv(up["data"], channel_name=up["name"], subscribers=up["subscribers"], role=role)
    raise ValueError(f"Неизвестная площадка: {platform!r}")


def _source_label(src: dict) -> str:
    if src.get("platform") == "csv":
        up = _uploads.get(src.get("upload_id", ""), {})
        return f"CSV «{up.get('name', '?')}»"
    return f"{src.get('platform')}: {src.get('id')}"


def _compare_warnings(author, comp, author_days: int) -> list[str]:
    out = []
    name = comp.display_name
    a, c = author.subscribers, comp.subscribers
    if a and c:
        ratio = max(a, c) / max(1, min(a, c))
        if ratio >= SIZE_RATIO_WARN:
            bigger = "больше" if c > a else "меньше"
            out.append(f"«{name}» в {ratio:.0f} раз {bigger} вашего канала. Сравнение тем и форматов с таким каналом "
                       f"может вводить в заблуждение — лучше выбирать каналы сопоставимого размера и вашей тематики.")
    if comp.posts and author_days > 0:
        comp_days = (max(p.date for p in comp.posts) - min(p.date for p in comp.posts)).days
        if comp_days < author_days * 0.7:
            out.append(f"У «{name}» собраны посты только за {comp_days} дн. из {author_days} "
                       f"(ограничение {COMPETITOR_MAX_POSTS} постов) — его тренд менее надёжен.")
    return out


def _run_job(job_id: str, payload: dict):
    job = _jobs[job_id]

    def step(msg: str):
        job["steps"].append({"t": round(time.time() - job["started"], 1), "msg": msg})

    try:
        job["status"] = "running"
        now = None
        if payload.get("demo"):
            step("Загружаю демо-данные (синтетические каналы)…")
            author, competitors = demo_channels()
            now = DEMO_NOW
        else:
            max_posts = int(min(max(int(payload.get("max_posts", 200)), 30), 600))
            vk_token = payload.get("vk_token") or os.getenv("VK_SERVICE_TOKEN", "")
            step(f"Собираю канал автора ({_source_label(payload['author'])})…")
            author = _collect(payload["author"], "author", max_posts, vk_token)
            step(f"Получено постов: {len(author.posts)}")
            competitors = []
            # период конкурентов выравниваем по периоду автора, иначе сравнение некорректно
            since = min(p.date for p in author.posts) if author.posts else None
            author_days = (max(p.date for p in author.posts) - since).days if author.posts else 0
            for src in (payload.get("competitors") or [])[:MAX_COMPETITORS]:
                if not (src.get("id") or src.get("upload_id")):
                    continue
                try:
                    step(f"Собираю конкурента ({_source_label(src)}) за тот же период, что и автора…")
                    ch = _collect(src, "competitor", COMPETITOR_MAX_POSTS, vk_token, since=since)
                    if src.get("platform") == "csv" and since:
                        ch.posts = [p for p in ch.posts if p.date >= since]
                    competitors.append(ch)
                    step(f"Получено постов: {len(ch.posts)}")
                    job["warnings"].extend(_compare_warnings(author, ch, author_days))
                except (TelegramError, VKError, CSVError, ValueError) as e:
                    job["warnings"].append(f"Конкурент {_source_label(src)} пропущен: {e}")
                    step(f"⚠ {e}")
            if len(author.posts) < 15:
                raise ValueError(f"У канала автора слишком мало постов для анализа ({len(author.posts)})")

        step("Считаю метрики, индекс виральности и статистическую значимость…")
        analysis = analyze(author, competitors, now=now)
        step(f"Тематическое моделирование: {len(analysis['topics']['list'])} тем; фактов в базе: {len(analysis['facts'])}")

        llm = llm_from_env()
        strategy = run_strategy(analysis, llm=llm, progress=step)
        job["result"] = {"analysis": analysis, "strategy": strategy,
                         "demo": bool(payload.get("demo")), "warnings": job["warnings"],
                         "generated_at": datetime.now(timezone.utc).isoformat()}
        step("Готово ✓")
        job["status"] = "done"
    except (TelegramError, VKError, CSVError, ValueError) as e:
        job["status"], job["error"] = "error", str(e)
    except Exception as e:      # непредвиденная ошибка — показываем пользователю кратко, в лог — целиком
        traceback.print_exc()
        job["status"], job["error"] = "error", f"Внутренняя ошибка: {e}"


# ------------------------------------------------------------------ API

@app.get("/")
def index():
    return send_from_directory(STATIC, "index.html", max_age=0)


@app.get("/static/<path:name>")
def static_files(name):
    return send_from_directory(STATIC, name)


@app.get("/api/config")
def config():
    llm = llm_from_env()
    return _json({"llm": {"enabled": llm is not None, "model": llm.name if llm else None},
                  "vk_token_configured": bool(os.getenv("VK_SERVICE_TOKEN"))})


@app.post("/api/upload")
def upload():
    f = request.files.get("file")
    if not f:
        return _json({"error": "файл не передан"}, 400)
    data = f.read()
    name = (request.form.get("name") or f.filename or "CSV").strip()[:60]
    subs = request.form.get("subscribers")
    try:
        subscribers = int(subs) if subs else None
        ch = load_csv(data, channel_name=name, subscribers=subscribers)
    except (CSVError, ValueError) as e:
        return _json({"error": str(e)}, 400)
    uid = uuid.uuid4().hex[:12]
    _remember(_uploads, uid, {"data": data, "name": name, "subscribers": subscribers}, 100)
    return _json({"upload_id": uid, "name": name, "posts": len(ch.posts)})


@app.post("/api/jobs")
def create_job():
    payload = request.get_json(silent=True) or {}
    if not payload.get("demo"):
        a = payload.get("author") or {}
        if not (a.get("id") or a.get("upload_id")):
            return _json({"error": "Укажите канал автора"}, 400)
    job_id = uuid.uuid4().hex[:12]
    _remember(_jobs, job_id, {"status": "queued", "steps": [], "warnings": [], "error": None,
                              "result": None, "started": time.time()}, MAX_JOBS)
    threading.Thread(target=_run_job, args=(job_id, payload), daemon=True).start()
    return _json({"job_id": job_id}, 202)


@app.get("/api/jobs/<job_id>")
def get_job(job_id):
    job = _jobs.get(job_id)
    if not job:
        abort(404)
    out = {k: job[k] for k in ("status", "steps", "warnings", "error")}
    if job["status"] == "done":
        out["result"] = job["result"]
    return _json(out)


@app.get("/api/jobs/<job_id>/report.md")
def report(job_id):
    job = _jobs.get(job_id)
    if not job or job["status"] != "done":
        abort(404)
    md = render_markdown(job["result"])
    return Response(md, mimetype="text/markdown; charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename=vektor-report.md"})


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("werkzeug").setLevel(logging.WARNING)   # не засоряем терминал строками опроса
    port = int(os.getenv("PORT", "8000"))
    app.run(host="0.0.0.0", port=port, threaded=True)


if __name__ == "__main__":
    main()
