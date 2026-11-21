"""Timers: cron conversion, label handling, and the Edit payload."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mihome_pc.domain import timer  # noqa: E402

INFO = {
    "scene_id": "1859030616805355520",
    "uid": "1000000001",
    "home_id": "200000000001",
    "name": "\u516b\u70b9\u56db\u5341\u4e94\u63d0\u9192",
    "enable": True,
    "scene_trigger": {"express": 1, "triggers": [{
        "src": "timer", "key": "timer.crontab",
        "name": "08:45 \u6cd5\u5b9a\u5de5\u4f5c\u65e5(\u4e2d\u56fd\u5927\u9646)",
        "value_json": "0 45 8 * * * *",
        "extra_json": {"time_zone": "Asia/Shanghai"}}]},
    "scene_action": {"mode": 1, "actions": [{"payload_json": {"did": "1"}}]},
    "create_time": "1732062412",
    "last_edit_time": "1760175194",
}


class TestCron(unittest.TestCase):
    def test_build(self):
        self.assertEqual(timer.cron_for(8, 45), "0 45 8 * * * *")
        self.assertEqual(timer.cron_for(0, 0), "0 0 0 * * * *")

    def test_weekly_days(self):
        self.assertEqual(timer.cron_for(7, 30, [1, 3]), "0 30 7 * * 1,3 *")
        # 0 = Sunday, 6 = Saturday (POSIX, matches the app's bitmasks)
        self.assertEqual(timer.cron_for(9, 0, [0, 6]), "0 0 9 * * 0,6 *")
        self.assertEqual(timer.cron_for(9, 0, [3, 1, 1]), "0 0 9 * * 1,3 *")

    def test_weekly_rejects_bad_day(self):
        with self.assertRaises(ValueError):
            timer.cron_for(9, 0, [7])

    def test_filters(self):
        self.assertEqual(timer.filter_for("workday"), "cn_workday")
        self.assertEqual(timer.filter_for("holiday"), "cn_freeday")
        self.assertEqual(timer.filter_for("daily"), "")
        self.assertEqual(timer.filter_for(None), "")

    def test_labels(self):
        self.assertEqual(timer.label_for(7, 30, [1, 3]), "07:30 \u5468\u4e00\u3001\u5468\u4e09")
        self.assertEqual(timer.label_for(7, 30, [], "workday"),
                         "07:30 \u6cd5\u5b9a\u5de5\u4f5c\u65e5")
        self.assertEqual(timer.label_for(7, 30), "07:30 \u6bcf\u5929")

    def test_parse(self):
        self.assertEqual(timer.clock_from_cron("0 45 8 * * * *"), (8, 45))
        self.assertIsNone(timer.clock_from_cron("1"))
        self.assertIsNone(timer.clock_from_cron(""))
        self.assertIsNone(timer.clock_from_cron(None))

    def test_retime_keeps_weekdays_and_filters(self):
        self.assertEqual(timer.retime("0 30 7 * * 1,3 *", 8, 15),
                         "0 15 8 * * 1,3 *")
        self.assertEqual(timer.retime("0 45 8 * * * *", 9, 0),
                         "0 0 9 * * * *")
        self.assertEqual(timer.retime("garbage", 9, 5), "0 5 9 * * * *")

    def test_parse_survives_int(self):
        # value_json sometimes arrives as an int (sensor automations)
        self.assertIsNone(timer.clock_from_cron(1))

    def test_range_is_checked(self):
        with self.assertRaises(ValueError):
            timer.cron_for(24, 0)
        with self.assertRaises(ValueError):
            timer.cron_for(8, 60)


class TestNextRun(unittest.TestCase):
    def setUp(self):
        import datetime
        self.datetime = datetime
        # 2026-11-21 is a Saturday
        self.now = datetime.datetime(2026, 11, 21, 9, 0)

    def test_daily_rolls_to_tomorrow(self):
        self.assertEqual(timer.next_run("0 45 8 * * * *", self.now),
                         self.datetime.datetime(2026, 11, 22, 8, 45))

    def test_same_day_when_not_passed_yet(self):
        early = self.datetime.datetime(2026, 11, 21, 7, 0)
        self.assertEqual(timer.next_run("0 45 8 * * * *", early),
                         self.datetime.datetime(2026, 11, 21, 8, 45))

    def test_weekly_days(self):
        # Monday + Wednesday, from Saturday -> Monday
        self.assertEqual(timer.next_run("0 45 8 * * 1,3 *", self.now),
                         self.datetime.datetime(2026, 11, 23, 8, 45))

    def test_weekdays_only_skips_the_weekend(self):
        self.assertEqual(timer.next_run("0 45 8 * * * *", self.now,
                                        weekdays_only=True),
                         self.datetime.datetime(2026, 11, 23, 8, 45))

    def test_sunday_is_zero(self):
        # cron 0 = Sunday -> next Sunday is 2026-11-22
        self.assertEqual(timer.next_run("0 0 9 * * 0 *", self.now),
                         self.datetime.datetime(2026, 11, 22, 9, 0))

    def test_bad_input(self):
        self.assertIsNone(timer.next_run("garbage", self.now))
        self.assertIsNone(timer.next_run("0 45 8 * * *", self.now))
        self.assertIsNone(timer.next_run("0 45 99 * * * *", self.now))


class TestDescribe(unittest.TestCase):
    def test_detail_keeps_the_app_sentence(self):
        text = timer.describe({"cron": "0 45 8 * * * *",
                               "detail": "08:45 \u6cd5\u5b9a\u5de5\u4f5c\u65e5"})
        self.assertEqual(text, "08:45 \u6cd5\u5b9a\u5de5\u4f5c\u65e5")

    def test_missing_detail_falls_back_to_cron(self):
        self.assertEqual(timer.describe({"cron": "0 5 9 * * * *"}), "09:05")


class TestBuildPayload(unittest.TestCase):
    def test_retime_keeps_id_and_rewrites_clock(self):
        payload = timer.build_payload(INFO, hour=9, minute=5,
                                      home_id="h", owner_uid="42",
                                      scene_id="1859030616805355520")
        trigger = payload["scene_trigger"]["triggers"][0]
        self.assertEqual(trigger["value_json"], "0 5 9 * * * *")
        self.assertTrue(trigger["name"].startswith("09:05"))
        self.assertIn("\u6cd5\u5b9a\u5de5\u4f5c\u65e5", trigger["name"])
        self.assertEqual(payload["scene_id"], "1859030616805355520")
        self.assertEqual(payload["edit_from"], 1)
        self.assertEqual(payload["app_version"], 19)
        self.assertEqual(payload["owner_uid"], "42")
        self.assertEqual(payload["home_id"], "h")

    def test_read_only_fields_are_dropped(self):
        payload = timer.build_payload(INFO, hour=7, minute=0)
        for field in ("uid", "create_time", "last_edit_time", "icon_url"):
            self.assertNotIn(field, payload)

    def test_rename(self):
        payload = timer.build_payload(INFO, name="\u65b0\u540d\u5b57", hour=7, minute=30)
        self.assertEqual(payload["name"], "\u65b0\u540d\u5b57")

    def test_clone_without_scene_id(self):
        payload = timer.build_payload(INFO, hour=7, minute=30, scene_id="")
        self.assertNotIn("scene_id", payload)

    def test_rejects_empty_info(self):
        with self.assertRaises(ValueError):
            timer.build_payload({}, hour=1, minute=1)

    def test_rejects_scene_without_trigger(self):
        with self.assertRaises(ValueError):
            timer.build_payload({"name": "x", "scene_trigger": {}}, hour=1, minute=1)

    def test_retime_payload_keeps_the_weekday(self):
        info = {"name": "x", "scene_trigger": {"triggers": [{
            "src": "timer", "name": "07:30 \u5468\u4e00\u3001\u5468\u4e09",
            "value_json": "0 30 7 * * 1,3 *"}]}}
        payload = timer.build_payload(info, hour=8, minute=15)
        trigger = payload["scene_trigger"]["triggers"][0]
        self.assertEqual(trigger["value_json"], "0 15 8 * * 1,3 *")
        self.assertIn("08:15", trigger["name"])
        self.assertIn("\u5468\u4e00", trigger["name"])

    def test_input_is_not_mutated(self):
        before = INFO["scene_trigger"]["triggers"][0]["value_json"]
        timer.build_payload(INFO, hour=1, minute=2)
        self.assertEqual(INFO["scene_trigger"]["triggers"][0]["value_json"], before)


if __name__ == "__main__":
    unittest.main()
