"""Собирает статическую демо-страницу (docs/index.html) для GitHub Pages.

Страница содержит готовый результат анализа синтетических демо-данных
и работает без сервера. Запуск:  python scripts/build_static_demo.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from vektor.agents.pipeline import run_strategy  # noqa: E402
from vektor.analytics.engine import analyze  # noqa: E402
from vektor.app import _default  # noqa: E402
from vektor.demo import NOW, demo_channels  # noqa: E402
from vektor.report import render_markdown  # noqa: E402

STATIC = ROOT / "vektor" / "static"


def main():
    author, competitors = demo_channels()
    analysis = analyze(author, competitors, now=NOW)
    strategy = run_strategy(analysis)
    result = {"analysis": analysis, "strategy": strategy, "demo": True, "warnings": []}
    payload = {"result": result, "report": render_markdown(result)}
    data = json.dumps(payload, ensure_ascii=False, default=_default).replace("</", "<\\/")

    html = (STATIC / "index.html").read_text(encoding="utf-8")
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    html = html.replace('<link rel="stylesheet" href="/static/style.css">', f"<style>\n{css}\n</style>")
    html = html.replace('<script src="/static/app.js"></script>',
                        f"<script>window.VEKTOR_STATIC = {data};</script>\n<script>\n{js}\n</script>")
    out = ROOT / "docs" / "index.html"
    out.parent.mkdir(exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"Готово: {out} ({out.stat().st_size // 1024} КБ)")


if __name__ == "__main__":
    main()
