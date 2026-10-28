"""Проверяем, что аналитика находит закономерности, специально заложенные в синтетические данные."""
import unittest

from vektor import demo
from vektor.analytics.engine import analyze
from vektor.analytics.stemmer import stem


def run(seed):
    a = demo.make_channel("author", seed=seed)
    c = [demo.make_channel(k, seed=seed) for k in ("c1", "c2", "c3")]
    return analyze(a, c, now=demo.NOW)


def best(buckets):
    rel = [b for b in buckets if b["reliable"]]
    return max(rel, key=lambda b: b["median_virality"])["key"]


class StemmerTests(unittest.TestCase):
    def test_forms_collapse(self):
        self.assertEqual(len({stem(w) for w in ["нейросети", "нейросеть", "нейросетями"]}), 1)
        self.assertEqual(len({stem(w) for w in ["собеседование", "собеседования", "собеседованиях"]}), 1)
        self.assertEqual(stem("ёлка"), stem("елка"))


class DemoRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r = run(6)

    def test_video_is_best_format(self):
        self.assertEqual(best(self.r["author"]["by_media"]), "видео")

    def test_evening_is_best_slot(self):
        self.assertEqual(best(self.r["author"]["by_slot"]), "18–21")

    def test_interview_niche_found(self):
        gaps = " ".join(g["label"] for g in self.r["topic_performance"]["gaps"])
        self.assertIn("собесед", gaps)

    def test_facts_are_numbered_and_unique(self):
        ids = [f["id"] for f in self.r["facts"]]
        self.assertEqual(ids, [f"F{i}" for i in range(1, len(ids) + 1)])
        self.assertGreaterEqual(len(ids), 10)

    def test_fresh_posts_excluded(self):
        self.assertLess(self.r["author"]["n_analyzable"], self.r["author"]["n_posts"])

    def test_benchmark_has_all_channels(self):
        self.assertEqual(len(self.r["benchmark"]), 4)
        self.assertEqual(self.r["benchmark"][0]["role"], "author")


class RobustnessTests(unittest.TestCase):
    """На разных случайных наборах заложенные эффекты должны находиться стабильно."""

    def test_across_seeds(self):
        seeds = range(1, 9)
        video = slot = niche = 0
        for s in seeds:
            r = run(s)
            video += best(r["author"]["by_media"]) == "видео"
            slot += best(r["author"]["by_slot"]) == "18–21"
            niche += any("собесед" in g["label"] or "оффер" in g["label"] for g in r["topic_performance"]["gaps"])
        self.assertEqual(video, 8)
        self.assertGreaterEqual(slot, 6)
        self.assertGreaterEqual(niche, 7)


if __name__ == "__main__":
    unittest.main()
