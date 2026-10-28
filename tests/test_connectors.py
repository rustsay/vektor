import unittest
from datetime import timezone
from pathlib import Path

from vektor.connectors.csv_import import CSVError, load_csv
from vektor.connectors.telegram import TelegramError, normalize_username, parse_count, parse_page
from vektor.connectors.vk import VKError, fetch_wall, media_type_from_attachments, normalize_domain, post_from_item

FIX = Path(__file__).parent / "fixtures"


class TelegramTests(unittest.TestCase):
    def test_parse_count(self):
        self.assertEqual(parse_count("1.2K"), 1200)
        self.assertEqual(parse_count("3,4M"), 3_400_000)
        self.assertEqual(parse_count("987"), 987)
        self.assertEqual(parse_count("12.345"), 12345)
        self.assertIsNone(parse_count(""))
        self.assertIsNone(parse_count("abc"))

    def test_normalize_username(self):
        for raw in ["@testchan", "t.me/testchan", "https://t.me/s/testchan", "https://t.me/testchan/101"]:
            self.assertEqual(normalize_username(raw), "testchan")
        with self.assertRaises(TelegramError):
            normalize_username("not a channel!")

    def test_parse_page(self):
        info, posts = parse_page((FIX / "telegram_page.html").read_text(encoding="utf-8"), "testchan")
        self.assertEqual(info["title"], "Тестовый канал")
        self.assertEqual(info["subscribers"], 12300)
        self.assertEqual(len(posts), 2, "служебное сообщение должно быть пропущено")
        p1, p2 = posts
        self.assertEqual(p1.post_id, "101")
        self.assertEqual(p1.views, 4500)
        self.assertEqual(p1.reactions, 1235)
        self.assertEqual(p1.media_type, "photo")
        self.assertIn("Первая строка\nВторая строка", p1.text)
        self.assertEqual(p1.date.tzinfo, timezone.utc)
        self.assertEqual(p1.url, "https://t.me/testchan/101")
        self.assertEqual(p2.media_type, "video")
        self.assertEqual(p2.text, "Видео с разбором", "цитата из ответа не должна попадать в текст")
        self.assertEqual(p2.views, 987)


class FakeResp:
    def __init__(self, data):
        self._d = data
        self.status_code = 200

    def raise_for_status(self):
        pass

    def json(self):
        return self._d


class FakeVKSession:
    def __init__(self, n_posts=150):
        self.n = n_posts

    def get(self, url, params=None, timeout=None):
        method = url.rsplit("/", 1)[-1]
        if method == "groups.getById":
            return FakeResp({"response": {"groups": [{"name": "Паблик", "members_count": 5000}]}})
        if method == "wall.get":
            off, cnt = params["offset"], params["count"]
            # как настоящий VK: от новых постов к старым
            ids = [self.n - 1 - k for k in range(off, min(off + cnt, self.n))]
            items = [{"id": i, "owner_id": -1, "date": 1_760_000_000 + i * 3600, "text": f"пост {i}",
                      "views": {"count": 100 + i}, "likes": {"count": 5}, "comments": {"count": 1},
                      "reposts": {"count": 2}, "attachments": [{"type": "photo"}]}
                     for i in ids]
            return FakeResp({"response": {"count": self.n, "items": items}})
        return FakeResp({"error": {"error_code": 5, "error_msg": "auth"}})


class VKTests(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(normalize_domain("https://vk.com/club123"), "club123")
        self.assertEqual(normalize_domain("@durov"), "durov")

    def test_media(self):
        self.assertEqual(media_type_from_attachments(None), "text")
        self.assertEqual(media_type_from_attachments([{"type": "photo"}, {"type": "video"}]), "video")
        self.assertEqual(media_type_from_attachments([{"type": "clip"}]), "video")
        self.assertEqual(media_type_from_attachments([{"type": "market"}]), "other")

    def test_post_from_item(self):
        p = post_from_item({"id": 7, "owner_id": -5, "date": 1_760_000_000, "text": "t",
                            "views": {"count": 10}, "likes": {"count": 3}}, "pub")
        self.assertEqual(p.url, "https://vk.com/wall-5_7")
        self.assertEqual((p.views, p.reactions, p.comments), (10, 3, 0))

    def test_fetch_paginates(self):
        ch = fetch_wall("pub", token="x", max_posts=130, session=FakeVKSession(150), pause=0)
        self.assertEqual(len(ch.posts), 130)
        self.assertEqual(ch.subscribers, 5000)
        self.assertEqual(ch.title, "Паблик")

    def test_requires_token(self):
        with self.assertRaises(VKError):
            fetch_wall("pub", token="")


class CSVTests(unittest.TestCase):
    def test_russian_headers_semicolon(self):
        data = "Дата;Текст;Просмотры;Лайки;Формат\n01.10.2026 18:00;Привет;1 200;30;Видео\n02.10.2026 10:00;Пока;900;10;фото\n"
        ch = load_csv(data.encode("utf-8-sig"), "Мой канал", subscribers=5000)
        self.assertEqual(len(ch.posts), 2)
        self.assertEqual(ch.posts[0].views, 1200)
        self.assertEqual(ch.posts[0].media_type, "video")
        self.assertEqual(ch.posts[0].date.hour, 15, "время без зоны трактуется как МСК")

    def test_missing_columns(self):
        with self.assertRaises(CSVError):
            load_csv("a,b\n1,2\n", "x")


if __name__ == "__main__":
    unittest.main()


class SinceTests(unittest.TestCase):
    def test_vk_since_filters_old_posts(self):
        from datetime import datetime, timezone
        since = datetime.fromtimestamp(1_760_000_000 + 100 * 3600, tz=timezone.utc)
        ch = fetch_wall("pub", token="x", max_posts=1000, session=FakeVKSession(150), pause=0, since=since)
        self.assertTrue(all(p.date >= since for p in ch.posts))
        self.assertEqual(len(ch.posts), 50)

    def test_comparability_warnings(self):
        from datetime import datetime, timedelta, timezone
        from vektor.app import _compare_warnings
        from vektor.models import Channel, Post
        t0 = datetime(2026, 8, 1, tzinfo=timezone.utc)
        mk = lambda days: [Post("telegram", "c", str(i), t0 + timedelta(days=i), views=10) for i in range(days)]
        author = Channel("telegram", "me", subscribers=7400, posts=mk(70))
        big = Channel("telegram", "big", title="Большой", subscribers=404000, posts=mk(10))
        w = _compare_warnings(author, big, 69)
        self.assertEqual(len(w), 2)
        self.assertIn("в 55 раз больше", w[0])
