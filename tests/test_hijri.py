import contextlib
import datetime as dt
import io
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
import hijri_lib as lib  # noqa: E402
import hijri_update as cli  # noqa: E402

D = dt.date


def page(greg="23 نوفمبر 2026", hijri="13 جُمادى الآخرة 1448", weekday="الإثنين"):
    """Minimal page with the same header shape as dar-alifta.org/ar/prayer (BOM before the Hijri day)."""
    return ("<html><body><div class='top'>%s, %s – ﻿%s هـ</div>"
            "<div class='prayer_time_today'>مواقيت الصلاة ﻿%s هـ</div></body></html>"
            % (weekday, greg, hijri, hijri))


class ParseTests(unittest.TestCase):
    def test_normal_page(self):
        obs = lib.parse_page(page())
        self.assertEqual((obs.greg, obs.year, obs.month, obs.day), (D(2026, 11, 23), 1448, 6, 13))

    def test_esa_style_spelling_with_tatweel(self):
        obs = lib.parse_page(page(hijri="13 جماد الآخر 1448"))
        self.assertEqual(obs.month, 6)
        obs = lib.parse_page(page(hijri="21 جمـــاد الأول 1448"))
        self.assertEqual((obs.month, obs.day), (5, 21))

    def test_all_hijri_months(self):
        names = ["المحرم", "صفر", "ربيع الأول", "ربيع الآخر", "جمادى الأولى", "جمادى الآخرة",
                 "رجب", "شعبان", "رمضان", "شوال", "ذو القعدة", "ذو الحجة"]
        for number, name in enumerate(names, 1):
            with self.subTest(name):
                self.assertEqual(lib.parse_page(page(hijri="5 %s 1447" % name)).month, number)

    def test_all_gregorian_months(self):
        names = ["يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو", "يوليو",
                 "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر"]
        for number, name in enumerate(names, 1):
            with self.subTest(name):
                self.assertEqual(lib.parse_page(page(greg="1 %s 2026" % name)).greg, D(2026, number, 1))

    def test_html_entity_dash_as_on_the_real_page(self):
        html = ("<span>Hausa</span> <b>الإثنين, 23 نوفمبر 2026 &ndash; \ufeff13 جُمادى الآخرة 1448 هـ</b>"
                "<h2>مواقيت الصلاة \ufeff13 جُمادى الآخرة 1448 هـ</h2>")
        obs = lib.parse_page(html)
        self.assertEqual((obs.greg, obs.year, obs.month, obs.day), (D(2026, 11, 23), 1448, 6, 13))

    def test_arabic_indic_digits(self):
        obs = lib.parse_page(page(greg="٢٣ نوفمبر ٢٠٢٦", hijri="١٣ جمادى الآخرة ١٤٤٨"))
        self.assertEqual((obs.greg, obs.year, obs.day), (D(2026, 11, 23), 1448, 13))

    def test_missing_date(self):
        for html in ("", "<html>no date here</html>", "<p>مواقيت الصلاة</p>"):
            with self.assertRaises(lib.DataError):
                lib.parse_page(html)

    def test_unknown_month_name(self):
        with self.assertRaises(lib.DataError):
            lib.parse_page(page(hijri="13 شهر غريب 1448"))

    def test_edge_values_rejected(self):
        for hijri in ("0 رجب 1448", "31 رجب 1448", "13 رجب 1300", "13 رجب 1700"):
            with self.subTest(hijri):
                with self.assertRaises(lib.DataError):
                    lib.parse_page(page(hijri=hijri))
        for hijri in ("1 رجب 1448", "30 رجب 1448"):
            with self.subTest(hijri):
                self.assertEqual(lib.parse_page(page(hijri=hijri)).day, int(hijri.split()[0]))

    def test_impossible_gregorian_date(self):
        with self.assertRaises(lib.DataError):
            lib.parse_page(page(greg="31 نوفمبر 2026"))

    def test_conflicting_headers(self):
        html = page() + page(hijri="14 جمادى الآخرة 1448")
        with self.assertRaises(lib.DataError):
            lib.parse_page(html)


def obs(greg, y, m, d):
    return lib.Observation(greg, y, m, d)


class StateTests(unittest.TestCase):
    def seeded(self):
        state = lib.empty_state()
        lib.apply_observation(state, obs(D(2026, 11, 23), 1448, 6, 13), D(2026, 11, 23))
        return state

    def test_seed_derives_month_start(self):
        state = self.seeded()
        self.assertEqual(state["months"][0]["start"], "2026-11-11")
        self.assertEqual(state["latest"]["day"], 13)

    def test_same_observation_changes_nothing(self):
        state = self.seeded()
        self.assertFalse(lib.apply_observation(state, obs(D(2026, 11, 23), 1448, 6, 13), D(2026, 11, 23)))

    def test_should_fetch_by_counting(self):
        state = self.seeded()  # day 13 on 2026-11-23
        cases = [(0, False), (1, False), (14, False), (15, True), (16, True), (17, True), (20, True)]
        for delta, expected in cases:
            with self.subTest(delta):
                today = D(2026, 11, 23) + dt.timedelta(days=delta)
                self.assertEqual(lib.should_fetch(state, today)[0], expected)
        self.assertTrue(lib.should_fetch(lib.empty_state(), D(2026, 11, 23))[0])

    def test_should_fetch_window_is_the_agreed_days(self):
        state = self.seeded()
        fetched = set()
        for delta in range(1, 31):
            expected_day = 13 + delta
            today = D(2026, 11, 23) + dt.timedelta(days=delta)
            if lib.should_fetch(state, today)[0]:
                fetched.add(expected_day)
        self.assertEqual({d for d in fetched if d <= 30}, {28, 29, 30})
        self.assertTrue(all(d > 30 for d in fetched - {28, 29, 30}))

    def test_clock_before_last_observation(self):
        with self.assertRaises(lib.DataError):
            lib.should_fetch(self.seeded(), D(2026, 11, 22))

    def test_month_rollover_sets_previous_length(self):
        for length in (29, 30):
            with self.subTest(length):
                state = self.seeded()
                new_start = D(2026, 11, 11) + dt.timedelta(days=length)
                self.assertTrue(lib.apply_observation(state, obs(new_start, 1448, 7, 1), new_start))
                self.assertEqual(state["months"][0]["length"], length)
                self.assertEqual(state["months"][1]["start"], new_start.isoformat())

    def test_observation_on_day_three_gives_same_start(self):
        state = self.seeded()
        start = D(2026, 12, 10)
        lib.apply_observation(state, obs(start + dt.timedelta(days=2), 1448, 7, 3), start + dt.timedelta(days=2))
        self.assertEqual(state["months"][1]["start"], "2026-12-10")
        self.assertEqual(state["months"][0]["length"], 29)

    def test_year_rollover(self):
        state = lib.empty_state()
        lib.apply_observation(state, obs(D(2027, 6, 1), 1448, 12, 1), D(2027, 6, 1))
        new_start = D(2027, 6, 30)
        lib.apply_observation(state, obs(new_start, 1449, 1, 1), new_start)
        self.assertEqual([(m["year"], m["month"]) for m in state["months"]], [(1448, 12), (1449, 1)])
        self.assertEqual(state["months"][0]["length"], 29)

    def test_conflicting_month_start(self):
        state = self.seeded()
        with self.assertRaises(lib.DataError):
            lib.apply_observation(state, obs(D(2026, 11, 24), 1448, 6, 13), D(2026, 11, 24))

    def test_impossible_month_length(self):
        for gap in (28, 31, 35):
            with self.subTest(gap):
                state = self.seeded()
                start = D(2026, 11, 11) + dt.timedelta(days=gap)
                with self.assertRaises(lib.DataError):
                    lib.apply_observation(state, obs(start, 1448, 7, 1), start)

    def test_stale_page(self):
        with self.assertRaises(lib.DataError):
            lib.apply_observation(lib.empty_state(), obs(D(2026, 11, 22), 1448, 6, 12), D(2026, 11, 23))

    def test_missed_months_do_not_invent_lengths(self):
        state = self.seeded()
        start = D(2027, 2, 1)
        lib.apply_observation(state, obs(start, 1448, 9, 1), start)
        self.assertIsNone(state["months"][0]["length"])
        self.assertEqual(len(state["months"]), 2)

    def test_state_file_round_trip_and_bad_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "hijri.json")
            self.assertEqual(lib.load_state(path), lib.empty_state())
            state = self.seeded()
            lib.save_state(path, state)
            self.assertEqual(lib.load_state(path), state)
            with open(path, "w") as f:
                f.write("{not json")
            with self.assertRaises(lib.DataError):
                lib.load_state(path)
            with open(path, "w") as f:
                json.dump({"schema": 99}, f)
            with self.assertRaises(lib.DataError):
                lib.load_state(path)


class CliTests(unittest.TestCase):
    def run_cli(self, path, today, fetch, *extra):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["--state", path, "--today", today, *extra], fetch=fetch)
        return code, out.getvalue(), err.getvalue()

    def test_first_run_then_skip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "hijri.json")
            calls = []
            def fetch():
                calls.append(1)
                return page()
            self.assertEqual(self.run_cli(path, "2026-11-23", fetch)[0], 0)
            self.assertEqual(len(calls), 1)
            self.assertEqual(self.run_cli(path, "2026-11-30", fetch)[0], 0)  # counted day 20, mid-month
            self.assertEqual(len(calls), 1)
            self.assertEqual(self.run_cli(path, "2026-11-30", fetch, "--force")[0], 2)  # page still says 23 Nov
            self.assertEqual(len(calls), 2)

    def test_fetch_failure_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "hijri.json")
            def fetch():
                raise lib.DataError("fetch failed: timeout")
            code, _, err = self.run_cli(path, "2026-11-23", fetch)
            self.assertEqual(code, 2)
            self.assertIn("fetch failed", err)
            self.assertFalse(os.path.exists(path))

    def test_wrong_page_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "hijri.json")
            for html in ("<html>changed layout</html>", page(greg="22 نوفمبر 2026")):
                code, _, _ = self.run_cli(path, "2026-11-23", lambda h=html: h)
                self.assertEqual(code, 2)
                self.assertFalse(os.path.exists(path))

    def test_bad_today_argument(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, _, err = self.run_cli(os.path.join(tmp, "h.json"), "not-a-date", lambda: page())
            self.assertEqual(code, 2)

    def test_window_day_updates_existing_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "hijri.json")
            self.assertEqual(self.run_cli(path, "2026-11-23", lambda: page())[0], 0)
            new_html = page(greg="11 ديسمبر 2026", hijri="1 رجب 1448", weekday="الجمعة")
            self.assertEqual(self.run_cli(path, "2026-12-11", lambda: new_html)[0], 0)
            state = lib.load_state(path)
            self.assertEqual([m["start"] for m in state["months"]], ["2026-11-11", "2026-12-11"])
            self.assertEqual(state["months"][0]["length"], 30)


if __name__ == "__main__":
    unittest.main()
