"""Компактная реализация стеммера Snowball для русского языка (без внешних зависимостей).

Алгоритм: https://snowballstem.org/algorithms/russian/stemmer.html
Нужен, чтобы «нейросети», «нейросеть» и «нейросетями» попадали в одну тему.
"""
from __future__ import annotations

import re
from functools import lru_cache

_VOWELS = set("аеиоуыэюя")


def _desc(items: str) -> list[str]:
    return sorted(items.split(), key=len, reverse=True)


_PG1 = _desc("в вши вшись")
_PG2 = _desc("ив ивши ившись ыв ывши ывшись")
_ADJ = _desc("ее ие ые ое ими ыми ей ий ый ой ем им ым ом его ого ему ому их ых ую юю ая яя ою ею")
_PART1 = _desc("ем нн вш ющ щ")
_PART2 = _desc("ивш ывш ующ")
_REFL = _desc("ся сь")
_VERB1 = _desc("ла на ете йте ли й л ем н ло но ет ют ны ть ешь нно")
_VERB2 = _desc("ила ыла ена ейте уйте ите или ыли ей уй ил ыл им ым ен ило ыло ено ят ует уют ит ыт ены ить ыть ишь ую ю")
_NOUN = _desc("а ев ов ие ье е иями ями ами еи ии и ией ей ой ий й иям ям ием ем ам ом о у ах иях ях ы ь ию ью ю ия ья я")
_DERIV = _desc("ост ость")
_SUPER = _desc("ейш ейше")


def _regions(word: str) -> tuple[int, int]:
    """Возвращает начало RV и R2."""
    rv = len(word)
    for i, ch in enumerate(word):
        if ch in _VOWELS:
            rv = i + 1
            break

    def next_region(start: int) -> int:
        for i in range(start + 1, len(word)):
            if word[i] not in _VOWELS and word[i - 1] in _VOWELS:
                return i + 1
        return len(word)

    r1 = next_region(0)
    r2 = next_region(r1) if r1 < len(word) else len(word)
    return rv, r2


def _strip(rv: str, group1: list[str], group2: list[str] | None = None) -> str | None:
    """Удаляет самое длинное окончание; group1 — только после «а»/«я»."""
    candidates = [(s, 1) for s in group1] + [(s, 2) for s in (group2 or [])]
    candidates.sort(key=lambda x: len(x[0]), reverse=True)
    for suf, grp in candidates:
        if rv.endswith(suf):
            if grp == 1 and group2 is not None:
                if len(rv) > len(suf) and rv[-len(suf) - 1] in "ая":
                    return rv[: -len(suf)]
                continue
            return rv[: -len(suf)]
    return None


@lru_cache(maxsize=50_000)
def stem(word: str) -> str:
    word = word.lower().replace("ё", "е")
    if len(word) < 3 or not re.fullmatch(r"[а-я-]+", word):
        return word
    rv_start, r2_start = _regions(word)
    prefix, rv = word[:rv_start], word[rv_start:]

    # Шаг 1
    res = _strip(rv, _PG1, _PG2)
    if res is not None:
        rv = res
    else:
        res = _strip(rv, _REFL)
        if res is not None:
            rv = res
        res = _strip(rv, _ADJ)
        if res is not None:
            rv = res
            res2 = _strip(rv, _PART1, _PART2)
            if res2 is not None:
                rv = res2
        else:
            res = _strip(rv, _VERB1, _VERB2)
            if res is not None:
                rv = res
            else:
                res = _strip(rv, _NOUN)
                if res is not None:
                    rv = res

    # Шаг 2
    if rv.endswith("и"):
        rv = rv[:-1]

    # Шаг 3: словообразовательные окончания в R2
    r2_in_rv = max(0, r2_start - rv_start)
    for suf in _DERIV:
        if rv.endswith(suf) and len(rv) - len(suf) >= r2_in_rv:
            rv = rv[: -len(suf)]
            break

    # Шаг 4
    if rv.endswith("нн"):
        rv = rv[:-1]
    else:
        res = _strip(rv, _SUPER)
        if res is not None:
            rv = res
            if rv.endswith("нн"):
                rv = rv[:-1]
        elif rv.endswith("ь"):
            rv = rv[:-1]

    return prefix + rv
