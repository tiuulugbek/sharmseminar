import hashlib
import hmac
import importlib.util
import json
import os
import sqlite3
import tempfile
import time
import unittest
from urllib.parse import urlencode


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
        os.environ["SECRET"] = "test-secret"
        os.environ["ADMIN_IDS"] = "555"
        os.environ["BOT_TOKEN"] = "123:TESTBOTTOKEN"
        server.init_db()
        self.client = server.app.test_client()
        self.headers = {"X-Bot-Token": "test-token"}

    def sql(self, statement, *params):
        con = sqlite3.connect(server.DB_PATH)
        con.execute(statement, params)
        con.commit(); con.close()

    def one(self, statement, *params):
        con = sqlite3.connect(server.DB_PATH)
        row = con.execute(statement, params).fetchone()
        con.close()
        return row

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

    # ------------------------------------------------------ tokens & QR pages
    def test_token_is_passport_derived_stable_and_hides_the_passport(self):
        person = self.register("Token Person", "0000123")
        token = person["token"]
        self.assertEqual(len(token), 16)
        self.assertEqual(token, server.make_token("FA", "0000123", person["id"]))
        # Re-running the migration must not hand out a different token.
        server.init_db()
        self.assertEqual(self.one("SELECT token FROM participants WHERE id=?", person["id"])[0], token)
        # The public page opens by token and by id, and leaks no passport.
        by_token = self.client.get(f"/api/p/{token}")
        by_id = self.client.get(f"/api/p/{person['id']}")
        self.assertEqual(by_token.status_code, 200)
        self.assertEqual(by_id.status_code, 200)
        self.assertEqual(by_token.get_json()["participant"]["id"], person["id"])
        self.assertNotIn("0000123", by_token.get_data(as_text=True))
        self.assertEqual(self.client.get("/api/p/nosuchtoken").status_code, 404)

    def test_token_differs_per_secret(self):
        person = self.register("Secret Person", "0000124")
        os.environ["SECRET"] = "another-secret"
        self.assertNotEqual(server.make_token("FA", "0000124", person["id"]), person["token"])
        os.environ["SECRET"] = "test-secret"

    # ------------------------------------------------------------------ roles
    def build_group(self):
        """Group 1 with a leader and two members, group 2 with one member."""
        leader = self.register("Leader One", "0000001", "9001")
        member = self.register("Member One", "0000002", "9100")
        other = self.register("Other Group", "0000003", "9200")
        self.sql("UPDATE participants SET grp=1,leader=1 WHERE id=?", leader["id"])
        self.sql("UPDATE participants SET grp=1 WHERE id=?", member["id"])
        self.sql("UPDATE participants SET grp=2 WHERE id=?", other["id"])
        return leader, member, other

    def whoami(self, telegram_id):
        return self.client.get(f"/api/bot/whoami?telegram_id={telegram_id}",
                               headers=self.headers).get_json()

    def test_whoami_ranks_admin_over_leader_over_member(self):
        leader, member, _ = self.build_group()
        self.assertEqual(self.whoami("555")["role"], "admin")
        self.assertEqual(self.whoami("9001")["role"], "leader")
        self.assertEqual(self.whoami("9100")["role"], "member")
        self.assertEqual(self.whoami("404040")["role"], "guest")
        self.assertEqual(self.whoami("9001")["group_name"], "Sazanchik")
        # An admin who is also on the participant list stays an admin.
        self.sql("UPDATE participants SET telegram_id='555' WHERE id=?", member["id"])
        self.assertEqual(self.whoami("555")["role"], "admin")

    # --------------------------------------------------------------- check-in
    def test_checkin_is_limited_to_admins_and_the_leaders_own_group(self):
        leader, member, other = self.build_group()
        ok = self.post("/api/bot/checkin", {
            "token": member["token"], "checkpoint": "seminar", "by_telegram_id": "9001"})
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.get_json()["participant"]["id"], member["id"])

        for who, payload in (("leader over another group", {"token": other["token"], "by_telegram_id": "9001"}),
                             ("member", {"token": member["token"], "by_telegram_id": "9100"}),
                             ("stranger", {"token": member["token"], "by_telegram_id": "404040"})):
            response = self.post("/api/bot/checkin", dict(payload, checkpoint="seminar"))
            self.assertEqual(response.status_code, 403, who)

        self.assertEqual(self.post("/api/bot/checkin", {
            "token": other["token"], "checkpoint": "seminar", "by_telegram_id": "555"}).status_code, 200)
        self.assertEqual(self.post("/api/bot/checkin", {
            "token": member["token"], "checkpoint": "nosuch", "by_telegram_id": "555"}).status_code, 400)

    def test_leader_scope_all_lets_leaders_scan_anybody(self):
        _, _, other = self.build_group()
        self.client.post("/api/pagebase", json={"base": "x"})  # unrelated setting stays intact
        con = sqlite3.connect(server.DB_PATH)
        con.execute("UPDATE settings SET value='\"all\"' WHERE key='leader_scope'")
        con.commit(); con.close()
        self.assertEqual(self.post("/api/bot/checkin", {
            "token": other["token"], "checkpoint": "seminar", "by_telegram_id": "9001"}).status_code, 200)

    # -------------------------------------------------------------- messaging
    def send(self, telegram_id, scope, text, value=None):
        return self.post("/api/bot/message", {
            "from_telegram_id": telegram_id, "scope": scope,
            "value": value, "text": text})

    def test_admin_broadcast_reaches_everyone_and_replies_come_back_to_admin(self):
        leader, member, other = self.build_group()
        sent = self.send("555", "all", "hammaga")
        self.assertEqual(sent.status_code, 200)
        self.assertEqual({r["id"] for r in sent.get_json()["recipients"]},
                         {leader["id"], member["id"], other["id"]})

        msg_id = sent.get_json()["message_id"]
        answer = self.post("/api/bot/reply", {
            "from_telegram_id": "9100", "parent_msg_id": msg_id, "text": "javob"})
        self.assertEqual(answer.status_code, 200)
        body = answer.get_json()
        self.assertEqual(body["to_telegram_id"], "555")
        self.assertIn(member["id"], body["sender_label"])
        self.assertIn("Member One", body["sender_label"])

    def test_leader_may_only_write_to_their_own_group(self):
        leader, member, other = self.build_group()
        mine = self.send("9001", "group", "guruhimga")
        self.assertEqual(mine.status_code, 200)
        self.assertEqual([r["id"] for r in mine.get_json()["recipients"]], [member["id"]])

        self.assertEqual(self.send("9001", "group", "x", value=2).status_code, 403)
        self.assertEqual(self.send("9001", "all", "x").status_code, 403)
        self.assertEqual(self.send("9001", "one", "x", value=other["id"]).status_code, 403)

        answer = self.post("/api/bot/reply", {
            "from_telegram_id": "9100", "parent_msg_id": mine.get_json()["message_id"],
            "text": "javob"})
        self.assertEqual(answer.get_json()["to_telegram_id"], "9001")

    def test_member_can_only_ask_their_leader_and_gets_the_answer_back(self):
        leader, member, other = self.build_group()
        self.assertEqual(self.send("9100", "all", "x").status_code, 403)
        self.assertEqual(self.send("9100", "group", "x").status_code, 403)
        self.assertEqual(self.send("9100", "one", "x", value=other["id"]).status_code, 403)

        question = self.send("9100", "leader", "savol")
        self.assertEqual(question.status_code, 200)
        self.assertEqual([r["telegram_id"] for r in question.get_json()["recipients"]], ["9001"])

        answer = self.post("/api/bot/reply", {
            "from_telegram_id": "9001", "parent_msg_id": question.get_json()["message_id"],
            "text": "javob"})
        self.assertEqual(answer.get_json()["to_telegram_id"], "9100")

    def test_outsiders_cannot_reply_and_delivery_is_recorded(self):
        leader, member, other = self.build_group()
        mine = self.send("9001", "group", "guruhimga").get_json()
        self.assertEqual(self.post("/api/bot/reply", {
            "from_telegram_id": "9200", "parent_msg_id": mine["message_id"],
            "text": "x"}).status_code, 403)
        self.assertEqual(self.post("/api/bot/reply", {
            "from_telegram_id": "9100", "parent_msg_id": 99999, "text": "x"}).status_code, 404)

        self.post("/api/bot/message/sent", {"message_id": mine["message_id"], "results": [
            {"id": member["id"], "telegram_id": "9100", "telegram_msg_id": "42", "ok": True}]})
        self.assertEqual(self.one("SELECT telegram_msg_id,status FROM msg_targets WHERE msg_id=?",
                                  mine["message_id"]), ("42", "sent"))
        self.assertEqual(self.one("SELECT delivered FROM messages WHERE id=?", mine["message_id"])[0], 1)

    def test_member_questions_are_copied_to_admins_when_enabled(self):
        self.build_group()
        con = sqlite3.connect(server.DB_PATH)
        con.execute("UPDATE settings SET value='true' WHERE key='copy_member_questions'")
        con.commit(); con.close()
        self.assertEqual(self.send("9100", "leader", "savol").get_json()["copy_to"], ["555"])

    # ------------------------------------------------------- WebApp initData
    def init_data(self, telegram_id, bot_token="123:TESTBOTTOKEN", auth_date=None):
        fields = {"auth_date": str(auth_date or int(time.time())),
                  "user": json.dumps({"id": telegram_id, "username": "tester"})}
        check = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
        secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
        fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
        return urlencode(fields)

    def test_webapp_rejects_forged_or_stale_init_data(self):
        self.build_group()
        self.assertEqual(self.client.post("/api/webapp/whoami", json={}).status_code, 401)
        self.assertEqual(self.client.post("/api/webapp/whoami", json={
            "init_data": "user=%7B%22id%22%3A555%7D&hash=deadbeef"}).status_code, 401)
        self.assertEqual(self.client.post("/api/webapp/whoami", json={
            "init_data": self.init_data(9001, bot_token="999:WRONG")}).status_code, 401)
        self.assertEqual(self.client.post("/api/webapp/whoami", json={
            "init_data": self.init_data(9001, auth_date=int(time.time()) - 90000)}).status_code, 401)

    def test_webapp_checkin_trusts_only_the_signed_identity(self):
        leader, member, other = self.build_group()
        signed_leader = self.init_data(9001)
        response = self.client.post("/api/webapp/whoami", json={"init_data": signed_leader})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["role"], "leader")

        ok = self.client.post("/api/webapp/checkin", json={
            "init_data": signed_leader, "token": member["token"], "checkpoint": "seminar"})
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.get_json()["participant"]["id"], member["id"])

        # A signed member cannot check anybody in, not even themselves.
        denied = self.client.post("/api/webapp/checkin", json={
            "init_data": self.init_data(9100), "token": member["token"], "checkpoint": "seminar"})
        self.assertEqual(denied.status_code, 403)
        # Nor can a leader reach another group.
        self.assertEqual(self.client.post("/api/webapp/checkin", json={
            "init_data": signed_leader, "token": other["token"],
            "checkpoint": "seminar"}).status_code, 403)

    # --------------------------------------------------------------- rosters
    def test_group_endpoints_report_leaders_and_attendance(self):
        leader, member, _ = self.build_group()
        self.post("/api/bot/checkin", {"token": member["token"], "checkpoint": "seminar",
                                       "by_telegram_id": "555"})
        groups = self.client.get("/api/bot/groups", headers=self.headers).get_json()["groups"]
        first = next(g for g in groups if g["id"] == 1)
        self.assertEqual(first["name"], "Sazanchik")
        self.assertEqual(first["leader"]["id"], leader["id"])
        self.assertEqual((first["total"], first["checked_in"]), (2, 1))

        roster = self.client.get("/api/bot/group/1", headers=self.headers).get_json()
        self.assertEqual(roster["members"][0]["id"], leader["id"])  # leader first
        self.assertIn("seminar", next(m for m in roster["members"]
                                      if m["id"] == member["id"])["checkins"])

    def test_saving_groups_moves_the_leader_flag(self):
        leader, member, _ = self.build_group()
        self.client.post("/api/groups", json={"groups": [
            {"id": 1, "name": "Sazanchik", "leader": member["id"]}]})
        self.assertEqual(self.one("SELECT leader FROM participants WHERE id=?", member["id"])[0], 1)
        self.assertEqual(self.one("SELECT leader FROM participants WHERE id=?", leader["id"])[0], 0)

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
