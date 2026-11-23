"""Track the Hijri date published by Dar al-Ifta (https://www.dar-alifta.org/ar/prayer).

The page header shows the Gregorian date and the official Hijri date, for example:
    "Monday, 23 November 2026 - 13 Jumada al-Akhira 1448" (in Arabic).

State file (docs/hijri.json) keeps month starts only. The app derives every day inside
a month from its start date, so a month is either fully confirmed or not at all.
"""
import datetime as dt
import html as html_lib
import json
import re
import unicodedata
import urllib.error
import urllib.request

SOURCE_URL = "https://www.dar-alifta.org/ar/prayer"
SOURCE_NAME = "dar-alifta"
USER_AGENT = "MawaqitMisrBot/1.0 (+https://data.ahmedhatem.work)"
SCHEMA = 1
# Hijri days (expected, by counting) on which a month boundary can change.
WINDOW_DAYS = {28, 29, 30, 1, 2, 3}


class DataError(Exception):
    """Source page or state is not usable. Nothing may be published."""


_MARKS = re.compile("[ً-ٰٟـ​-‏‪-‮⁦-⁩﻿]")
_LETTERS = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ى": "ي", "ة": "ه"})
_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")


def clean(text):
    """Remove diacritics, tatweel and invisible marks; unify letter variants and digits."""
    text = unicodedata.normalize("NFKC", text)
    text = _MARKS.sub("", text).translate(_LETTERS).translate(_DIGITS)
    return re.sub(r"\s+", " ", text).strip()


def _key(name):
    return clean(name).replace(" ", "")


_HIJRI_MONTHS = {
    1: ["محرم", "المحرم"],
    2: ["صفر"],
    3: ["ربيع الأول", "ربيع اول"],
    4: ["ربيع الآخر", "ربيع الآخرة", "ربيع الثاني", "ربيع الثانية"],
    5: ["جمادى الأولى", "جمادى الأول", "جماد الأول", "جماد الأولى"],
    6: ["جمادى الآخرة", "جمادى الآخر", "جماد الآخر", "جماد الآخرة", "جمادى الثانية", "جمادى الثاني"],
    7: ["رجب"],
    8: ["شعبان"],
    9: ["رمضان"],
    10: ["شوال"],
    11: ["ذو القعدة", "ذي القعدة"],
    12: ["ذو الحجة", "ذي الحجة"],
}
HIJRI_BY_KEY = {_key(n): m for m, names in _HIJRI_MONTHS.items() for n in names}

_GREGORIAN_MONTHS = {
    "يناير": 1, "فبراير": 2, "مارس": 3, "أبريل": 4, "مايو": 5, "يونيو": 6, "يوليو": 7,
    "أغسطس": 8, "سبتمبر": 9, "أكتوبر": 10, "نوفمبر": 11, "ديسمبر": 12,
}
GREGORIAN_BY_KEY = {_key(n): m for n, m in _GREGORIAN_MONTHS.items()}

# "<day> <Gregorian month> <year> - <day> <Hijri month words> <year> hijri-suffix"
_HEADER = re.compile(
    r"(\d{1,2}) ([^\d\-–—,]+?) (\d{4}) ?[-–—] ?(\d{1,2}) ([^\d\-–—,]+?) (\d{4}) ?ه(?!\w)"
)  # "ه" is the Hijri suffix after clean() has removed its tatweel


class Observation:
    def __init__(self, greg, year, month, day):
        self.greg = greg
        self.year = year
        self.month = month
        self.day = day

    def __repr__(self):
        return "Observation(%s -> %d-%02d-%02d H)" % (self.greg, self.year, self.month, self.day)


def _strip_tags(html):
    html = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
    return html_lib.unescape(re.sub(r"<[^>]+>", " ", html))


def parse_page(html):
    """Return the Observation shown in the page header. Raises DataError if absent or odd."""
    text = clean(_strip_tags(html))
    found = []
    for m in _HEADER.finditer(text):
        gd, gname, gy, hd, hname, hy = m.groups()
        gmonth = GREGORIAN_BY_KEY.get(_key(gname))
        hmonth = HIJRI_BY_KEY.get(_key(hname))
        if gmonth is None or hmonth is None:
            raise DataError("unknown month name: %r / %r" % (gname, hname))
        try:
            greg = dt.date(int(gy), gmonth, int(gd))
        except ValueError as exc:
            raise DataError("bad Gregorian date: %s" % exc)
        found.append(Observation(greg, int(hy), hmonth, int(hd)))
    if not found:
        raise DataError("Hijri date not found in the page")
    first = found[0]
    for other in found[1:]:
        if (other.greg, other.year, other.month, other.day) != (first.greg, first.year, first.month, first.day):
            raise DataError("the page shows different dates: %r vs %r" % (first, other))
    if not 1 <= first.day <= 30 or not 1400 <= first.year <= 1600:
        raise DataError("Hijri date out of range: %r" % first)
    return first


def fetch_page(url=SOURCE_URL):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read().decode("utf-8")
    except (urllib.error.URLError, OSError, UnicodeDecodeError) as exc:
        raise DataError("fetch failed: %s" % exc)


# ---- state -----------------------------------------------------------------

def empty_state():
    return {"schema": SCHEMA, "source": SOURCE_URL, "latest": None, "months": []}


def load_state(path):
    try:
        with open(path, encoding="utf-8") as f:
            state = json.load(f)
    except FileNotFoundError:
        return empty_state()
    except json.JSONDecodeError as exc:
        raise DataError("state file is not valid JSON: %s" % exc)
    if state.get("schema") != SCHEMA or "months" not in state:
        raise DataError("unknown state schema")
    return state


def save_state(path, state):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1, sort_keys=True)
        f.write("\n")


def _prev_key(year, month):
    return (year - 1, 12) if month == 1 else (year, month - 1)


def _next_key(year, month):
    return (year + 1, 1) if month == 12 else (year, month + 1)


def _index(state):
    return {(m["year"], m["month"]): m for m in state["months"]}


def should_fetch(state, today):
    """Decide by counting days, so the site is visited only around month boundaries."""
    latest = state.get("latest")
    if not latest:
        return True, "no data yet"
    last = dt.date.fromisoformat(latest["date"])
    delta = (today - last).days
    if delta < 0:
        raise DataError("today (%s) is before the last observation (%s)" % (today, last))
    if delta == 0:
        return False, "already recorded today"
    expected = latest["day"] + delta
    if 4 <= expected <= 27:
        return False, "mid-month (counted day %d), no check needed" % expected
    return True, "counted day %d is inside the check window" % expected


def apply_observation(state, obs, today):
    """Merge one observation into state. Returns True if state changed."""
    if obs.greg != today:
        raise DataError("page date %s is not today (%s): stale page" % (obs.greg, today))
    start = obs.greg - dt.timedelta(days=obs.day - 1)
    before = json.dumps(state, sort_keys=True)
    months = _index(state)
    key = (obs.year, obs.month)
    if key in months:
        if dt.date.fromisoformat(months[key]["start"]) != start:
            raise DataError("conflict: month %d-%02d starts %s, the page implies %s"
                            % (obs.year, obs.month, months[key]["start"], start))
    else:
        rec = {"year": obs.year, "month": obs.month, "start": start.isoformat(),
               "length": None, "sources": [SOURCE_NAME]}
        prev = months.get(_prev_key(*key))
        if prev is not None:
            length = (start - dt.date.fromisoformat(prev["start"])).days
            if length not in (29, 30):
                raise DataError("month %d-%02d would last %d days" % (prev["year"], prev["month"], length))
            if prev["length"] not in (None, length):
                raise DataError("month %d-%02d length changed" % (prev["year"], prev["month"]))
            prev["length"] = length
        nxt = months.get(_next_key(*key))
        if nxt is not None:
            length = (dt.date.fromisoformat(nxt["start"]) - start).days
            if length not in (29, 30):
                raise DataError("month %d-%02d would last %d days" % (obs.year, obs.month, length))
            rec["length"] = length
        state["months"].append(rec)
        state["months"].sort(key=lambda m: (m["year"], m["month"]))
    latest = state.get("latest")
    if not latest or dt.date.fromisoformat(latest["date"]) < obs.greg:
        state["latest"] = {"date": obs.greg.isoformat(), "year": obs.year, "month": obs.month, "day": obs.day}
    return json.dumps(state, sort_keys=True) != before
