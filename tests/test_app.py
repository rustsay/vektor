import io
import time
import unittest

from vektor.app import app


class AppTests(unittest.TestCase):
    def setUp(self):
        self.c = app.test_client()

    def wait(self, job_id, timeout=30):
        t0 = time.time()
        while time.time() - t0 < timeout:
            j = self.c.get(f"/api/jobs/{job_id}").get_json()
            if j["status"] in ("done", "error"):
                return j
            time.sleep(0.2)
        self.fail("задача не завершилась")

    def test_index(self):
        r = self.c.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Вектор".encode(), r.data)

    def test_demo_job_and_report(self):
        job = self.c.post("/api/jobs", json={"demo": True}).get_json()["job_id"]
        j = self.wait(job)
        self.assertEqual(j["status"], "done", j.get("error"))
        self.assertEqual(len(j["result"]["strategy"]["directions"]), 3)
        md = self.c.get(f"/api/jobs/{job}/report.md")
        self.assertEqual(md.status_code, 200)
        self.assertIn("## 3 направления развития", md.data.decode())

    def test_requires_author(self):
        r = self.c.post("/api/jobs", json={"author": {"platform": "telegram", "id": ""}})
        self.assertEqual(r.status_code, 400)

    def test_csv_flow(self):
        rows = ["date,text,views,likes,type"]
        for i in range(60):
            rows.append(f"2026-0{1 + i // 20}-{1 + i % 20:02d} 1{i % 10}:00,пост номер {i} про нейросети и код,{800 + (i * 37) % 500},{10 + i % 7},{'video' if i % 5 == 0 else 'text'}")
        up = self.c.post("/api/upload", data={"file": (io.BytesIO("\n".join(rows).encode()), "my.csv"),
                                              "name": "Мой канал", "subscribers": "3000"},
                         content_type="multipart/form-data").get_json()
        self.assertEqual(up["posts"], 60)
        job = self.c.post("/api/jobs", json={"author": {"platform": "csv", "upload_id": up["upload_id"]}}).get_json()["job_id"]
        j = self.wait(job)
        self.assertEqual(j["status"], "done", j.get("error"))
        self.assertEqual(j["result"]["analysis"]["author"]["channel"]["subscribers"], 3000)

    def test_bad_csv(self):
        r = self.c.post("/api/upload", data={"file": (io.BytesIO(b"a,b\n1,2"), "x.csv")}, content_type="multipart/form-data")
        self.assertEqual(r.status_code, 400)


if __name__ == "__main__":
    unittest.main()
