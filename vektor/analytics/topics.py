"""Тематическое моделирование: какие темы есть у автора и конкурентов и как они заходят.

TF-IDF по стеммированным словам и биграммам → NMF (неотрицательная
матричная факторизация). Каждая тема описывается словами-маркерами,
каждому посту присваивается доминирующая тема.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict

import numpy as np
import pandas as pd
from sklearn.decomposition import NMF
from sklearn.feature_extraction.text import TfidfVectorizer

from .stemmer import stem

STOPWORDS = set("""
и в во не что он на я с со как а то все она так его но да ты к у же вы за бы по только ее мне было вот от меня
еще нет о из ему теперь когда даже ну вдруг ли если уже или ни быть был него до вас нибудь опять уж вам ведь там
потом себя ничего ей может они тут где есть надо ней для мы тебя их чем была сам чтоб без будто чего раз тоже себе
под будет ж тогда кто этот того потому этого какой совсем ним здесь этом один почти мой тем чтобы нее сейчас были
куда зачем всех никогда можно при наконец два об другой хоть после над больше тот через эти нас про всего них
какая много разве три эту моя впрочем хорошо свою этой перед иногда лучше чуть том нельзя такой им более всегда
конечно всю между это эта также очень свой свои своих которые который которая которых которой весь всё просто
есть будут будем был были может можем могу нужно надо вообще ещё сегодня завтра вчера год года лет день дня
вот вон тут там здесь кстати например типа короче именно поэтому который чтобы просто тоже ваш ваша ваши наш
наша наши мой моя мои твой твоя другие другой других каждый каждая каждого любой многие несколько сколько
подписывайтесь подписаться канал канале канала ссылка ссылке ссылку комментариях комментарии пост поста посте
http https www com ru org t me vk html
""".split())

TOKEN_RE = re.compile(r"[a-zа-яё][a-zа-яё\-]{2,}", re.I)


class Tokenizer:
    """Токенизация + стемминг. Запоминает самые частые словоформы для подписи тем."""

    def __init__(self):
        self.surface: dict[str, Counter] = defaultdict(Counter)

    def words(self, text: str) -> list[str]:
        text = re.sub(r"https?://\S+", " ", text.lower().replace("ё", "е"))
        out = []
        for w in TOKEN_RE.findall(text):
            w = w.strip("-")
            if len(w) < 3 or w in STOPWORDS or w.isdigit():
                continue
            s = stem(w)
            if len(s) < 3 or s in STOPWORDS:
                continue
            self.surface[s][w] += 1
            out.append(s)
        return out

    def __call__(self, text: str) -> list[str]:
        ws = self.words(text)
        return ws + [f"{a} {b}" for a, b in zip(ws, ws[1:])]

    def label(self, term: str) -> str:
        parts = term.split(" ")
        return " ".join(self.surface[p].most_common(1)[0][0] if self.surface.get(p) else p for p in parts)


SENT_SPLIT = re.compile(r"(?<=[.!?…])\s+|\n+")


def strip_boilerplate(df: pd.DataFrame, min_share: float = 0.2, min_count: int = 5) -> pd.Series:
    """Убирает «подписи» — фразы, которые повторяются в значительной доле постов канала
    («Подписывайтесь…», «Сохраняйте…»). Иначе они образуют ложные «темы»."""
    cleaned = df["text"].copy()
    for _, grp in df.groupby("channel_name"):
        sents = grp["text"].map(lambda t: [s.strip() for s in SENT_SPLIT.split(t) if s.strip()])
        counts = Counter(s for lst in sents for s in set(lst))
        limit = max(min_count, min_share * len(grp))
        boiler = {s for s, c in counts.items() if c >= limit and len(s) > 10}
        if boiler:
            cleaned.loc[grp.index] = sents.map(lambda lst: " ".join(s for s in lst if s not in boiler))
    return cleaned


def _n_topics(n_docs: int) -> int:
    return int(np.clip(round(np.sqrt(n_docs / 8)), 4, 10))


def fit_topics(frames: list[pd.DataFrame], min_chars: int = 40, n_topics: int | None = None,
               random_state: int = 0) -> dict:
    """frames — подготовленные датафреймы каналов (prepare_frame). Возвращает темы и разметку постов."""
    all_df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if all_df.empty:
        return {"topics": [], "assignments": pd.DataFrame(), "ok": False, "reason": "нет постов"}
    all_df = all_df.copy()
    all_df["text"] = strip_boilerplate(all_df)
    all_df["length"] = all_df["text"].str.len()
    docs = all_df[all_df["length"] >= min_chars]
    if len(docs) < 30:
        return {"topics": [], "assignments": pd.DataFrame(), "ok": False,
                "reason": f"слишком мало текстовых постов ({len(docs)}) для тематического анализа"}

    tok = Tokenizer()
    vec = TfidfVectorizer(analyzer=tok, min_df=max(2, int(0.005 * len(docs))), max_df=0.5,
                          sublinear_tf=True)
    X = vec.fit_transform(docs["text"].tolist())
    if X.shape[1] < 10:
        return {"topics": [], "assignments": pd.DataFrame(), "ok": False, "reason": "слишком бедный словарь"}

    k = n_topics or _n_topics(len(docs))
    nmf = NMF(n_components=k, init="nndsvda", random_state=random_state, max_iter=500)
    W = nmf.fit_transform(X)
    H = nmf.components_
    terms = np.array(vec.get_feature_names_out())

    topics = []
    for t in range(k):
        idx = H[t].argsort()[::-1]
        top_terms, seen_stems = [], set()
        for i in idx:
            term = terms[i]
            stems = set(term.split(" "))
            if stems & seen_stems:
                continue
            seen_stems |= stems
            top_terms.append(tok.label(term))
            if len(top_terms) == 6:
                break
        topics.append({"id": t, "label": ", ".join(top_terms[:3]), "keywords": top_terms})

    strength = W.max(axis=1)
    threshold = np.quantile(strength[strength > 0], 0.15) if (strength > 0).any() else 0
    assigned = np.where(strength > threshold, W.argmax(axis=1), -1)
    assignments = docs[["channel_name", "role", "post_id", "virality", "views", "analyzable",
                        "text", "media_type"]].copy()
    assignments["topic"] = assigned
    return {"topics": topics, "assignments": assignments.reset_index(drop=True), "ok": True,
            "n_docs": int(len(docs)), "n_topics": k}


def topic_performance(topic_result: dict) -> dict:
    """Для каждой темы: доля и медианная виральность у автора и у конкурентов + возможности."""
    if not topic_result.get("ok"):
        return {"ok": False, "reason": topic_result.get("reason"), "table": [], "gaps": [],
                "strong": [], "weak": []}
    a = topic_result["assignments"]
    a = a[a["topic"] >= 0]
    labels = {t["id"]: t for t in topic_result["topics"]}

    def stats(sub: pd.DataFrame, tid: int) -> dict:
        tot = len(sub)
        g = sub[sub["topic"] == tid]
        ga = g[g["analyzable"]]
        return {
            "n": int(len(g)),
            "share": round(len(g) / tot, 3) if tot else 0.0,
            "median_virality": round(float(ga["virality"].median()), 2) if len(ga) else None,
        }

    author = a[a["role"] == "author"]
    comp = a[a["role"] == "competitor"]
    table = []
    for tid, t in labels.items():
        row = {"topic_id": tid, "label": t["label"], "keywords": t["keywords"],
               "author": stats(author, tid), "competitors": stats(comp, tid) if len(comp) else None}
        # примеры лучших постов конкурентов по теме — для контент-стратега
        if len(comp):
            ex = comp[(comp["topic"] == tid) & comp["analyzable"]].sort_values("virality", ascending=False).head(2)
            row["competitor_examples"] = [
                {"channel": r.channel_name, "virality": round(float(r.virality), 2),
                 "snippet": r.text[:160].replace("\n", " ")}
                for r in ex.itertuples()
            ]
        ex_a = author[(author["topic"] == tid) & author["analyzable"]].sort_values("virality", ascending=False).head(2)
        row["author_examples"] = [
            {"virality": round(float(r.virality), 2), "snippet": r.text[:160].replace("\n", " ")}
            for r in ex_a.itertuples()
        ]
        table.append(row)

    gaps, strong, weak = [], [], []
    for row in table:
        au, co = row["author"], row["competitors"]
        if co and co["n"] >= MIN_N and co["share"] >= 0.07 and (co["median_virality"] or 0) >= 1.0 \
                and au["share"] < co["share"] / 2:
            gaps.append(row)
        if au["n"] >= MIN_N and au["median_virality"] is not None:
            if au["median_virality"] >= 1.15:
                strong.append(row)
            elif au["median_virality"] <= 0.85:
                weak.append(row)
    gaps.sort(key=lambda r: r["competitors"]["share"] * (r["competitors"]["median_virality"] or 0), reverse=True)
    strong.sort(key=lambda r: r["author"]["median_virality"], reverse=True)
    weak.sort(key=lambda r: r["author"]["median_virality"])
    return {"ok": True, "table": table, "gaps": gaps, "strong": strong, "weak": weak}


MIN_N = 4
