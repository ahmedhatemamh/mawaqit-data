import contextlib
import datetime as dt
import io
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
import bayan_lib as bayan  # noqa: E402
import bayan_update as cli  # noqa: E402
import hijri_lib as lib  # noqa: E402

D = dt.date


def statement(month="رمضان", year=1446, weekday="السبت", ordinal="الأول", gmonth="مارس"):
    """Minimal page with the same title and announcement sentence as a real statement."""
    return ("<html><body><h1>بيان دار الإفتاء المصرية حول رؤية هلال شهر %s لعام %dهـ</h1>"
            "<p>وعلى ذلك تُعلن دارُ الإفتاءِ المصريةُ أن يومَ %s الموافق %s من شهر %s لعام ألفين "
            "وخمسة وعشرين ميلاديًّا هو أول أيام شهر %s لعام %d هجريًّا.</p></body></html>"
            % (month, year, weekday, ordinal, gmonth, month, year))


def sitemap(*ids):
    urls = "".join(
        "<url><loc>https://www.dar-alifta.org/ar/Articles/Details/%d/بيان-دار-الإفتاء-المصرية-حول-رؤية-هلال-شهر-%s</loc></url>" % (i, i)
        for i in ids)
    other = "<url><loc>https://www.dar-alifta.org/ar/Articles/Details/5/خبر-آخر</loc></url>"
    return ('<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">%s%s</urlset>'
            % (other, urls))


class ParseStatementTests(unittest.TestCase):
    def test_normal(self):
        self.assertEqual(bayan.parse_statement(statement()), (1446, 9, D(2025, 3, 1)))

    def test_numeric_day_and_other_month(self):
        # 1 Shawwal 1446 = Sunday 30 March 2025 in the official statements' template (Friday-based check below)
        html = statement(month="شوال", weekday="الأحد", ordinal="30", gmonth="مارس")
        self.assertEqual(bayan.parse_statement(html), (1446, 10, D(2025, 3, 30)))

    def test_spelled_ordinals(self):
        html = statement(month="رجب", year=1447, weekday="الأحد", ordinal="الحادي والعشرين", gmonth="ديسمبر")
        self.assertEqual(bayan.parse_statement(html)[2], D(2025, 12, 21))

    def test_weekday_that_does_not_match_the_year_is_rejected(self):
        with self.assertRaises(lib.DataError):
            bayan.parse_statement(statement(weekday="الاثنين"))

    def test_missing_sentence(self):
        with self.assertRaises(lib.DataError):
            bayan.parse_statement("<h1>بيان دار الإفتاء المصرية حول رؤية هلال شهر رمضان لعام 1446هـ</h1>")

    def test_missing_title(self):
        with self.assertRaises(lib.DataError):
            bayan.parse_statement("<p>صفحة أخرى</p>")

    def test_unknown_month_names(self):
        with self.assertRaises(lib.DataError):
            bayan.parse_statement(statement(month="شهرغلط"))
        with self.assertRaises(lib.DataError):
            bayan.parse_statement(statement(gmonth="شهرغلط"))


class ListStatementsTests(unittest.TestCase):
    def test_only_moon_statements_oldest_first(self):
        self.assertEqual([i for i, _ in bayan.list_statements(sitemap(30, 10, 20))], [10, 20, 30])

    def test_bad_xml(self):
        with self.assertRaises(lib.DataError):
            bayan.list_statements("<urlset")

    def test_empty(self):
        self.assertEqual(bayan.list_statements(sitemap()), [])


def fetcher(pages):
    def fetch(url):
        for article_id, html in pages.items():
            if "/Details/%d/" % article_id in url:
                return html
        raise lib.DataError("fetch failed: %s" % url)
    return fetch


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.state = lib.empty_state()

    def sync(self, pages, ids=None):
        ids = ids if ids is not None else sorted(pages)
        return bayan.sync(self.state, fetcher(pages), lambda: sitemap(*ids))

    def test_records_new_statements_and_lengths(self):
        pages = {10: statement(), 20: statement(month="شوال", weekday="الأحد", ordinal="30", gmonth="مارس")}
        self.assertTrue(self.sync(pages))
        by = {(m["year"], m["month"]): m for m in self.state["months"]}
        self.assertEqual(by[(1446, 9)]["start"], "2025-03-01")
        self.assertEqual(by[(1446, 9)]["length"], 29)
        self.assertIsNone(by[(1446, 10)]["length"])
        self.assertEqual(by[(1446, 9)]["sources"], ["dar-alifta-bayan"])
        self.assertEqual(self.state["bayan_ids"], [10, 20])

    def test_nothing_new_changes_nothing(self):
        self.sync({10: statement()})
        before = json.dumps(self.state, sort_keys=True)
        self.assertFalse(self.sync({10: statement()}))
        self.assertEqual(json.dumps(self.state, sort_keys=True), before)

    def test_agreeing_month_gains_a_source(self):
        self.state["months"].append({"year": 1446, "month": 9, "start": "2025-03-01", "length": None,
                                     "sources": ["dar-alifta"]})
        self.sync({10: statement()})
        self.assertEqual(self.state["months"][0]["sources"], ["dar-alifta", "dar-alifta-bayan"])

    def test_disagreeing_month_stops_and_changes_nothing(self):
        self.state["months"].append({"year": 1446, "month": 9, "start": "2025-03-02", "length": None,
                                     "sources": ["dar-alifta"]})
        before = json.dumps(self.state, sort_keys=True)
        with self.assertRaises(lib.DataError):
            self.sync({10: statement()})
        self.assertEqual(json.dumps(self.state, sort_keys=True), before)

    def test_correction_in_the_same_batch_newest_wins(self):
        # two statements for one month: Friday 28 Feb first, then the corrected Saturday 1 March
        pages = {10: statement(weekday="الجمعة", ordinal="الثامن والعشرين", gmonth="فبراير"),
                 11: statement()}
        pages[10] = pages[10].replace("الثامن والعشرين", "28")
        self.assertTrue(self.sync(pages))
        self.assertEqual(self.state["months"][0]["start"], "2025-03-01")

    def test_correction_of_an_already_recorded_month_stops(self):
        self.sync({10: statement()})
        before = json.dumps(self.state, sort_keys=True)
        later = statement(weekday="الجمعة", ordinal="28", gmonth="فبراير")
        with self.assertRaises(lib.DataError):
            self.sync({10: statement(), 11: later}, ids=[10, 11])
        self.assertEqual(json.dumps(self.state, sort_keys=True), before)

    def test_fetch_failure_changes_nothing(self):
        before = json.dumps(self.state, sort_keys=True)
        with self.assertRaises(lib.DataError):
            self.sync({10: statement()}, ids=[10, 11])
        self.assertEqual(json.dumps(self.state, sort_keys=True), before)

    def test_impossible_spacing_between_months_stops(self):
        # Ramadan 1 March 2025, then Dhu al-Qadah claimed on 5 March: impossible
        self.sync({10: statement()})
        bad = statement(month="ذو القعدة", weekday="الأربعاء", ordinal="5", gmonth="مارس")
        with self.assertRaises(lib.DataError):
            self.sync({10: statement(), 20: bad}, ids=[10, 20])


class CliTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "hijri.json")

    def tearDown(self):
        self.dir.cleanup()

    def write(self, latest_date, day):
        state = lib.empty_state()
        state["latest"] = {"date": latest_date, "year": 1446, "month": 8, "day": day}
        lib.save_state(self.path, state)

    def run_cli(self, today, *extra):
        out = io.StringIO()
        calls = []
        pages = {10: statement()}

        def fetch_url(url):
            calls.append(url)
            return fetcher(pages)(url)
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = cli.main(["--state", self.path, "--today", today, *extra], fetch_url=fetch_url,
                            fetch_sitemap=lambda: sitemap(10), delay=lambda: None)
        return code, out.getvalue(), calls

    def test_mid_month_does_not_visit_the_site(self):
        self.write("2025-02-10", 11)
        code, out, calls = self.run_cli("2025-02-12")
        self.assertEqual((code, calls), (0, []))
        self.assertIn("skip", out)

    def test_inside_the_window_records(self):
        self.write("2025-02-10", 11)
        code, out, calls = self.run_cli("2025-02-28")
        self.assertEqual(code, 0)
        self.assertIn("updated", out)
        self.assertEqual(lib.load_state(self.path)["bayan_ids"], [10])

    def test_force_in_mid_month(self):
        self.write("2025-02-10", 11)
        code, out, _ = self.run_cli("2025-02-12", "--force")
        self.assertEqual(code, 0)
        self.assertIn("forced", out)

    def test_failure_exits_2_and_keeps_the_file(self):
        self.write("2025-02-10", 11)
        with open(self.path, encoding="utf-8") as f:
            before = f.read()
        out, err = io.StringIO(), io.StringIO()

        def broken(url):
            raise lib.DataError("fetch failed: boom")
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["--state", self.path, "--today", "2025-02-28"], fetch_url=broken,
                            fetch_sitemap=lambda: sitemap(10), delay=lambda: None)
        self.assertEqual(code, 2)
        self.assertIn("ERROR", err.getvalue())
        with open(self.path, encoding="utf-8") as f:
            self.assertEqual(f.read(), before)


if __name__ == "__main__":
    unittest.main()
