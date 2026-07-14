import importlib.util
import os
import sqlite3
import tempfile
import unittest


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPEC = importlib.util.spec_from_file_location("seminar_server", os.path.join(ROOT, "server.py"))
server = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(server)


class BotApiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        server.DB_PATH = os.path.join(self.tmp.name, "test.db")
        server.SEED_P = os.path.join(self.tmp.name, "missing.json")
        server._db_initialized = True
        os.environ["BOT_API_TOKEN"] = "test-token"
        server.init_db()
        self.client = server.app.test_client()
        self.headers = {"X-Bot-Token": "test-token"}

    def tearDown(self):
        self.tmp.cleanup()

    def post(self, path, data):
        return self.client.post(path, json=data, headers=self.headers)

    def register(self, fio, number, telegram_id=""):
        response = self.post("/api/bot/register", {
            "fio": fio, "passport_series": "FA", "passport_number": number,
            "dob": "01-02-2000", "xona_turi": "2 kishilik",
            "telegram_id": telegram_id,
        })
        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        return response.get_json()["participant"]

    def test_requires_token(self):
        self.assertEqual(self.client.get("/api/bot/stats").status_code, 401)

    def test_legacy_schema_migration_is_idempotent(self):
        legacy = os.path.join(self.tmp.name, "legacy.db")
        con = sqlite3.connect(legacy)
        con.execute("CREATE TABLE participants(id TEXT PRIMARY KEY,fio TEXT,jinsi TEXT,fuqarolik TEXT,"
                    "xona_turi TEXT,xona_guruhi TEXT,kelish TEXT,grp INTEGER,leader INTEGER DEFAULT 0,"
                    "room TEXT DEFAULT '',branch TEXT DEFAULT '',telegram TEXT DEFAULT '',roles TEXT DEFAULT '[]')")
        con.execute("CREATE TABLE checkins(pid TEXT,checkpoint TEXT,ts TEXT,PRIMARY KEY(pid,checkpoint))")
        con.execute("CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT)")
        con.execute("INSERT INTO participants(id,fio) VALUES('OLD-1','Old Person')")
        con.commit(); con.close()
        server.DB_PATH = legacy
        server.init_db(); server.init_db()
        con = sqlite3.connect(legacy)
        cols = {r[1] for r in con.execute("PRAGMA table_info(participants)")}
        self.assertIn("passport_series", cols)
        self.assertEqual(con.execute("SELECT fio FROM participants WHERE id='OLD-1'").fetchone()[0], "Old Person")
        con.close()

    def test_register_find_leading_zero_and_duplicate(self):
        person = self.register("Karimov Ali", "0012345")
        found = self.post("/api/bot/find", {"series": "FA12345"}).get_json()["participants"]
        self.assertEqual(found[0]["id"], person["id"])
        duplicate = self.post("/api/bot/register", {
            "fio": "Duplicate", "passport_series": "FA", "passport_number": "12345"})
        self.assertEqual(duplicate.status_code, 409)

    def test_dob_formats_and_telegram_conflict(self):
        one = self.register("One Person", "0000001")
        two = self.register("Two Person", "0000002")
        found = self.post("/api/bot/find", {"dob": "01.02.2000"}).get_json()["participants"]
        self.assertEqual(len(found), 2)
        self.assertEqual(self.post("/api/bot/link", {
            "id": one["id"], "telegram_id": "777"}).status_code, 200)
        self.assertEqual(self.post("/api/bot/link", {
            "id": two["id"], "telegram_id": "777"}).status_code, 409)

    def test_roommate_recipients_and_stats(self):
        one = self.register("One Person", "0000001", "101")
        self.register("Two Person", "0000002", "102")
        linked = self.post("/api/bot/roommate", {
            "series": "FA1", "roommate_series": "FA2"}).get_json()
        self.assertEqual(linked["status"], "linked")
        recipients = self.client.get("/api/bot/recipients", headers=self.headers).get_json()["recipients"]
        self.assertEqual(len(recipients), 2)
        self.assertEqual(recipients[0]["roommates"], ["Two Person"])
        stats = self.client.get("/api/bot/stats", headers=self.headers).get_json()
        self.assertEqual(stats["total"], 2)
        self.assertEqual(stats["rooms_ready"], 1)

    def test_badge_template_keeps_multiple_logo_and_font_settings(self):
        badge = {"w": 70, "h": 110, "bg": "#fff", "elements": [
            {"id": "logo1", "type": "logo", "src": "data:image/png;base64,AAA", "x": 1, "y": 1, "w": 20},
            {"id": "logo2", "type": "logo", "src": "data:image/png;base64,BBB", "x": 30, "y": 1, "w": 20},
            {"id": "text1", "type": "text", "text": "Seminar", "font": "Georgia,serif",
             "italic": True, "lineHeight": 1.2, "letter": 0.4},
        ]}
        self.assertEqual(self.client.post("/api/badge", json=badge).status_code, 200)
        saved = self.client.get("/api/bootstrap").get_json()["badge"]
        self.assertEqual(saved["elements"][1]["src"], "data:image/png;base64,BBB")
        self.assertEqual(saved["elements"][2]["font"], "Georgia,serif")


if __name__ == "__main__":
    unittest.main()
