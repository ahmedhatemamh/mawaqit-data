"""Official Dar al-Ifta moon-sighting statements: the first day of each Hijri month.

Each month the Dar al-Ifta publishes "بيان دار الإفتاء المصرية حول رؤية هلال شهر ..." whose
announcement sentence has a fixed template:

    يوم <weekday> <ordinal> من شهر <Gregorian month> ... هو أول أيام شهر <Hijri month>

The Gregorian year is not written. It is fixed by the statement's own Hijri year and checked
against the weekday, so a wrong or missing year is an error, never a guess.
"""
import copy
import datetime as dt
import re
import urllib.parse
import xml.etree.ElementTree as ET

import hijri_lib as lib

SITEMAP_URL = "https://www.dar-alifta.org/ar/sitemap.xml"
SOURCE_NAME = "dar-alifta-bayan"

_GREGORIAN = dict(lib.GREGORIAN_BY_KEY)
_GREGORIAN.update({"يونيه": 6, "يوليه": 7, "ابريل": 4})  # spellings used in older statements
_WEEKDAYS = {"الاثنين": 0, "الثلاثاء": 1, "الاربعاء": 2, "الخميس": 3, "الجمعه": 4, "السبت": 5, "الاحد": 6}

_UNITS = ["الاول", "الثاني", "الثالث", "الرابع", "الخامس", "السادس", "السابع", "الثامن", "التاسع"]
_ORDINALS = {"العاشر": 10, "العشرين": 20, "الثلاثين": 30, "الواحد والعشرين": 21, "الواحد والثلاثين": 31,
             "الحادي والثلاثين": 31}
for _i, _u in enumerate(_UNITS, 1):
    _ORDINALS[_u] = _i
    _ORDINALS[("الحادي" if _i == 1 else _u) + " عشر"] = 10 + _i
    _ORDINALS[("الحادي" if _i == 1 else _u) + " والعشرين"] = 20 + _i
_ORDINAL_RE = "|".join(sorted(map(re.escape, _ORDINALS), key=len, reverse=True))

_TITLE = re.compile(r"بيان دار الافتاء المصريه حول رؤيه هلال شهر (.+?) ل(?:عام|سنه) (\d{4})ه")
_SENTENCE = re.compile(r"يوم (\S+) (?:الموافق )?(%s|\d{1,2}) من شهر ([^\s:،,]+)[،,:]? .{0,80}?"
                       r"هو اول ايام (?:شهر |عيد الفطر|ذي |ذو )" % _ORDINAL_RE)


def _approx_gregorian_year(year, month):
    """Fractional Gregorian year of a Hijri (year, month); good to a few weeks."""
    return 622.54 + (year - 1) * 0.970224 + (month - 1) / 12 * 0.970224


def parse_statement(html):
    """Return (hijri_year, hijri_month, first_day) from one statement page. Raises DataError."""
    text = lib.clean(lib._strip_tags(html))
    title = _TITLE.search(text)
    if not title:
        raise lib.DataError("statement title not found")
    month = lib.HIJRI_BY_KEY.get(lib._key(title.group(1).replace("المبارك", "")))
    if month is None:
        raise lib.DataError("unknown Hijri month %r" % title.group(1))
    year = int(title.group(2))
    anchor = text.find("تعلن دار الافتاء المصريه")
    sentences = _SENTENCE.findall(text[anchor:] if anchor >= 0 else text)
    if not sentences:
        raise lib.DataError("first-day sentence not found")
    weekday_name, ordinal, gmonth_name = sentences[0]
    weekday = _WEEKDAYS.get(weekday_name)
    gmonth = _GREGORIAN.get(lib._key(gmonth_name))
    if weekday is None or gmonth is None:
        raise lib.DataError("unknown weekday or month: %r %r" % (weekday_name, gmonth_name))
    day = int(ordinal) if ordinal.isdigit() else _ORDINALS[ordinal]
    approx = _approx_gregorian_year(year, month)
    candidates = []
    for gyear in (int(approx) - 1, int(approx), int(approx) + 1):
        try:
            candidate = dt.date(gyear, gmonth, day)
        except ValueError:
            continue
        fraction = candidate.year + (candidate.timetuple().tm_yday - 1) / 366
        if candidate.weekday() == weekday and abs(fraction - approx) < 0.2:
            candidates.append(candidate)
    if len(candidates) != 1:
        raise lib.DataError("cannot fix the Gregorian year of %d-%02d: %s" % (year, month, candidates))
    return year, month, candidates[0]


def list_statements(sitemap_xml):
    """Return [(article_id, url)] of the moon-sighting statements in the sitemap, oldest id first."""
    try:
        root = ET.fromstring(sitemap_xml)
    except ET.ParseError as exc:
        raise lib.DataError("sitemap is not valid XML: %s" % exc)
    found = {}
    for loc in root.iter("{http://www.sitemaps.org/schemas/sitemap/0.9}loc"):
        url = (loc.text or "").strip()
        m = re.search(r"/Articles/Details/(\d+)/(.+)$", url)
        if not m:
            continue
        slug = lib.clean(urllib.parse.unquote(m.group(2)).replace("-", " "))
        if "بيان دار الافتاء المصريه حول رؤيه هلال شهر" in slug:
            found[int(m.group(1))] = url
    return sorted(found.items())


def sync(state, fetch_url, fetch_sitemap, delay=lambda: None):
    """Record every statement not seen yet. Returns True if state changed.

    state["bayan_ids"] lists the articles already processed. A statement for a month that is
    already recorded must agree with it; a disagreement (including an official correction) stops
    the run so a person looks at it. Nothing is written when anything fails.
    """
    seen = set(state.get("bayan_ids", []))
    inventory = list_statements(fetch_sitemap())
    if not inventory:
        # The archive is never empty: an error page or a different sitemap layout must not look like "nothing new".
        raise lib.DataError("the sitemap lists no moon-sighting statements: the site layout may have changed")
    todo = [(i, u) for i, u in inventory if i not in seen]
    if not todo:
        return False
    work = copy.deepcopy(state)
    parsed = []
    for article_id, url in todo:
        delay()
        parsed.append((article_id,) + parse_statement(fetch_url(url)))
    # A later statement for the same month is an official correction: the newest one wins,
    # but only among statements that are processed together (never against recorded data).
    newest = {}
    for article_id, year, month, start in parsed:
        newest[(year, month)] = (article_id, start)
    for (year, month), (_, start) in sorted(newest.items()):
        lib.insert_month(work, year, month, start, SOURCE_NAME)
    work["bayan_ids"] = sorted(seen | {i for i, _ in todo})
    state.clear()
    state.update(work)
    return True
