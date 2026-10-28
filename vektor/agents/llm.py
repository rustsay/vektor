"""Клиент для любой OpenAI-совместимой LLM (OpenAI, YandexGPT, GigaChat через прокси,
Ollama, vLLM, LM Studio). Без сторонних SDK — только requests.

Настройка через переменные окружения:
    VEKTOR_LLM_BASE_URL   например http://localhost:11434/v1 (Ollama)
    VEKTOR_LLM_API_KEY    ключ (для локальных моделей можно любой)
    VEKTOR_LLM_MODEL      имя модели
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Optional, Protocol

import requests


class LLMError(RuntimeError):
    pass


class ChatModel(Protocol):
    name: str

    def chat(self, messages: list[dict], temperature: float = 0.5) -> str: ...


log = logging.getLogger("vektor.llm")


@dataclass
class OpenAICompatibleLLM:
    base_url: str
    api_key: str
    model: str
    timeout: int = 300
    retries: int = 1
    json_mode: bool = True      # response_format=json_object — сильно повышает надёжность у локальных моделей
    parallel: int = 3           # сколько агентов опрашивать одновременно
    num_ctx: int = 8192         # окно контекста для Ollama (по умолчанию у неё 2–4 тыс. токенов — промпт обрезается)

    @property
    def is_ollama(self) -> bool:
        return ":11434" in self.base_url and os.getenv("VEKTOR_OLLAMA_NATIVE", "1") not in ("0", "false", "no")

    def _ollama_chat(self, messages: list[dict], temperature: float) -> str:
        """Родной API Ollama: только он позволяет задать num_ctx и строгий JSON-формат."""
        root = self.base_url.rstrip("/")
        if root.endswith("/v1"):
            root = root[:-3]
        payload = {"model": self.model, "messages": messages, "stream": False,
                   "options": {"temperature": temperature, "num_ctx": self.num_ctx}}
        if self.json_mode:
            payload["format"] = "json"
        t0 = time.time()
        try:
            r = requests.post(root + "/api/chat", json=payload, timeout=self.timeout)
        except requests.Timeout:
            raise LLMError(f"модель не ответила за {self.timeout} с — увеличьте VEKTOR_LLM_TIMEOUT")
        except requests.RequestException as e:
            raise LLMError(f"Ollama недоступна ({e}). Запущено ли приложение Ollama?")
        if r.status_code >= 400:
            raise LLMError(f"Ollama HTTP {r.status_code}: {r.text[:300]}")
        content = r.json().get("message", {}).get("content", "")
        log.info("Ollama ответила за %.1f с (%d символов)", time.time() - t0, len(content))
        return content

    @property
    def name(self) -> str:
        return self.model

    def chat(self, messages: list[dict], temperature: float = 0.5) -> str:
        if self.is_ollama:
            return self._ollama_chat(messages, temperature)
        url = self.base_url.rstrip("/") + "/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        last = None
        for attempt in range(self.retries + 1):
            payload = {"model": self.model, "messages": messages, "temperature": temperature}
            if self.json_mode:
                payload["response_format"] = {"type": "json_object"}
            t0 = time.time()
            try:
                r = requests.post(url, json=payload, headers=headers, timeout=self.timeout)
                if r.status_code >= 400:
                    # провайдер не поддерживает response_format — повторяем без него
                    if self.json_mode and r.status_code in (400, 422) and "response_format" in r.text:
                        log.warning("Модель не поддерживает JSON-режим, отключаю его")
                        self.json_mode = False
                        continue
                    raise LLMError(f"HTTP {r.status_code}: {r.text[:300]}")
                content = r.json()["choices"][0]["message"]["content"]
                log.info("LLM ответила за %.1f с (%d символов)", time.time() - t0, len(content or ""))
                return content
            except requests.Timeout as e:
                last = LLMError(f"модель не ответила за {self.timeout} с — увеличьте VEKTOR_LLM_TIMEOUT")
                log.warning("LLM: таймаут после %.0f с", time.time() - t0)
                break           # повтор после таймаута только удвоит ожидание
            except (requests.RequestException, LLMError, KeyError, ValueError) as e:
                last = e
                log.warning("LLM: ошибка (попытка %d): %s", attempt + 1, e)
                time.sleep(1.5 * (attempt + 1))
        raise LLMError(f"LLM недоступна: {last}")


def llm_from_env() -> Optional[OpenAICompatibleLLM]:
    base = os.getenv("VEKTOR_LLM_BASE_URL")
    model = os.getenv("VEKTOR_LLM_MODEL")
    if not base or not model:
        return None
    return OpenAICompatibleLLM(
        base_url=base,
        api_key=os.getenv("VEKTOR_LLM_API_KEY", "none"),
        model=model,
        timeout=int(os.getenv("VEKTOR_LLM_TIMEOUT", "300")),
        json_mode=os.getenv("VEKTOR_LLM_JSON_MODE", "1") not in ("0", "false", "no"),
        # локальная модель на ноутбуке быстрее отвечает на запросы по очереди
        parallel=int(os.getenv("VEKTOR_LLM_PARALLEL", "1" if _is_local(base) else "3")),
        num_ctx=int(os.getenv("VEKTOR_LLM_NUM_CTX", "8192")),
    )


def _is_local(url: str) -> bool:
    return any(h in url for h in ("localhost", "127.0.0.1", "0.0.0.0", "host.docker.internal"))


def extract_json(text: str) -> dict:
    """Достаёт JSON из ответа модели: из ```json-блока или по первой/последней фигурной скобке."""
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.S)
    candidate = m.group(1) if m else None
    if candidate is None:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise LLMError("В ответе модели нет JSON")
        candidate = text[start:end + 1]
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        # частая ошибка моделей — висячие запятые
        cleaned = re.sub(r",\s*([}\]])", r"\1", candidate)
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as e:
            raise LLMError(f"Невалидный JSON от модели: {e}") from e


def chat_json(llm: ChatModel, messages: list[dict], temperature: float) -> dict:
    """Запрос с одной повторной попыткой, если модель вернула не-JSON."""
    raw = llm.chat(messages, temperature=temperature)
    try:
        return extract_json(raw)
    except LLMError:
        retry = messages + [{"role": "assistant", "content": raw},
                            {"role": "user", "content": "Верни ответ строго одним валидным JSON-объектом по схеме, без пояснений."}]
        return extract_json(llm.chat(retry, temperature=0.1))
