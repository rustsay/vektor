import threading
import unittest

from werkzeug.serving import make_server

from tests.mock_llm import FakeLLM, make_app
from vektor import demo
from vektor.agents.llm import LLMError, OpenAICompatibleLLM, extract_json
from vektor.agents.pipeline import run_strategy, validate_recommendations
from vektor.analytics.engine import analyze


def demo_analysis():
    a, c = demo.demo_channels()
    return analyze(a, c, now=demo.NOW)


class ValidationTests(unittest.TestCase):
    def test_filter(self):
        recs = [{"title": "ok", "facts": ["F1", "F99"]}, {"title": "fake", "facts": ["F99"]}, {"title": "none", "facts": []}]
        valid, rejected = validate_recommendations(recs, {"F1", "F2"})
        self.assertEqual([r["title"] for r in valid], ["ok"])
        self.assertEqual(valid[0]["facts"], ["F1"])
        self.assertEqual(valid[0]["dropped_facts"], ["F99"])
        self.assertEqual({r["title"] for r in rejected}, {"fake", "none"})

    def test_extract_json(self):
        self.assertEqual(extract_json('текст ```json\n{"a": 1}\n``` ещё'), {"a": 1})
        self.assertEqual(extract_json('ответ: {"a": [1, 2,], }'), {"a": [1, 2]})
        with self.assertRaises(LLMError):
            extract_json("без json")


class PipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.analysis = demo_analysis()
        cls.ids = {f["id"] for f in cls.analysis["facts"]}

    def test_offline(self):
        s = run_strategy(self.analysis)
        self.assertEqual(s["mode"], "offline")
        self.assertEqual(len(s["directions"]), 3)
        for d in s["directions"]:
            self.assertTrue(set(d["facts"]) <= self.ids)
        self.assertGreater(len(s["content_plan"]), 3)

    def test_llm_hallucinations_are_rejected(self):
        s = run_strategy(self.analysis, llm=FakeLLM())
        self.assertEqual(s["mode"], "llm")
        reasons = {r["title"]: (r["rejected_by"], r["reject_reason"]) for r in s["rejected"]}
        self.assertEqual(reasons["Запустить TikTok"][0], "проверка ссылок")
        self.assertIn("F99", reasons["Запустить TikTok"][1])
        self.assertEqual(reasons["Чаще использовать эмодзи"][0], "проверка ссылок")
        self.assertEqual(reasons["Повысить частоту публикаций"][0], "агент-критик")
        self.assertEqual(reasons["Выдуманное направление"][0], "проверка ссылок")
        titles = [d["title"] for d in s["directions"]]
        self.assertNotIn("Выдуманное направление", titles)
        self.assertEqual(len(s["directions"]), 3)
        for d in s["directions"]:
            self.assertTrue(set(d["facts"]) <= self.ids)

    def test_llm_failure_falls_back(self):
        s = run_strategy(self.analysis, llm=FakeLLM(fail=True))
        self.assertTrue(all(a["fallback"] for a in s["agents"]))
        self.assertIsNotNone(s["critic_error"])
        self.assertEqual(len(s["directions"]), 3)


class HTTPClientTests(unittest.TestCase):
    """Настоящий HTTP-запрос к OpenAI-совместимому эндпоинту (заглушке)."""

    @classmethod
    def setUpClass(cls):
        cls.server = make_server("127.0.0.1", 0, make_app())
        cls.port = cls.server.server_port
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def test_ollama_native(self):
        import os
        from vektor.agents.llm import llm_from_env
        # имитируем Ollama: порт 11434 → родной /api/chat; подменяем адрес на заглушку
        llm = OpenAICompatibleLLM(base_url=f"http://127.0.0.1:{self.port}/v1", api_key="x", model="mock")
        llm.__class__ = type("OllamaLike", (OpenAICompatibleLLM,), {"is_ollama": property(lambda self: True)})
        s = run_strategy(demo_analysis(), llm=llm)
        self.assertEqual(s["mode"], "llm", s["llm_errors"])
        os.environ.update({"VEKTOR_LLM_BASE_URL": "http://localhost:11434/v1", "VEKTOR_LLM_MODEL": "qwen2.5:7b"})
        try:
            cfg = llm_from_env()
            self.assertTrue(cfg.is_ollama)
            self.assertEqual(cfg.parallel, 1)
        finally:
            for k in ("VEKTOR_LLM_BASE_URL", "VEKTOR_LLM_MODEL"):
                os.environ.pop(k, None)

    def test_end_to_end(self):
        llm = OpenAICompatibleLLM(base_url=f"http://127.0.0.1:{self.port}/v1", api_key="x", model="mock")
        s = run_strategy(demo_analysis(), llm=llm)
        self.assertFalse(any(a["fallback"] for a in s["agents"]))
        self.assertIsNone(s["critic_error"])
        self.assertEqual(len(s["directions"]), 3)


if __name__ == "__main__":
    unittest.main()


class PartialFailLLM(FakeLLM):
    """Агенты отвечают, а критик падает — режим должен стать «смешанным»."""

    def chat(self, messages, temperature=0.5):
        if "агент-критик" in messages[0]["content"]:
            raise RuntimeError("таймаут")
        return super().chat(messages, temperature)


class ModeTests(unittest.TestCase):
    def test_mixed_mode_reported_honestly(self):
        from vektor.report import mode_text
        s = run_strategy(demo_analysis(), llm=PartialFailLLM())
        self.assertEqual(s["mode"], "mixed")
        self.assertEqual(s["llm_steps_ok"], 3)
        self.assertEqual(s["llm_errors"][0]["step"], "Агент-критик")
        self.assertIn("3 из 4", mode_text(s))

    def test_all_failed_is_offline(self):
        from vektor.report import mode_text
        s = run_strategy(demo_analysis(), llm=FakeLLM(fail=True))
        self.assertEqual(s["mode"], "offline")
        self.assertIn("не ответила", mode_text(s))
