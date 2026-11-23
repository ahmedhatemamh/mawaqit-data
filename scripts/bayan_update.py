"""Daily job: record new official Dar al-Ifta moon-sighting statements in docs/hijri.json.

Runs around Hijri month boundaries only (the same counted-day window as hijri_update.py), so the
sitemap is read a handful of days per month. Run it BEFORE hijri_update.py: both decide from the
state's latest observation, and hijri_update.py moves it.
Exit codes: 0 = ok (changed or nothing to do), 2 = failure (nothing was written).
"""
import argparse
import datetime as dt
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bayan_lib as bayan  # noqa: E402
import hijri_lib as lib  # noqa: E402

PAUSE_SECONDS = 4  # between statement pages, to stay polite


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": lib.USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.read().decode("utf-8")
    except (urllib.error.URLError, OSError, UnicodeDecodeError) as exc:
        raise lib.DataError("fetch failed: %s" % exc)


def cairo_today():
    from zoneinfo import ZoneInfo
    return dt.datetime.now(ZoneInfo("Africa/Cairo")).date()


def run(state_path, today, force=False, fetch_url=_get, fetch_sitemap=None, delay=lambda: time.sleep(PAUSE_SECONDS)):
    fetch_sitemap = fetch_sitemap or (lambda: fetch_url(bayan.SITEMAP_URL))
    state = lib.load_state(state_path)
    need, reason = lib.should_fetch(state, today)
    if not need and not force:
        print("skip: " + reason)
        return False
    print("check: " + ("forced" if force and not need else reason))
    changed = bayan.sync(state, fetch_url, fetch_sitemap, delay)
    if changed:
        lib.save_state(state_path, state)
    print("updated" if changed else "no new statements")
    return changed


def main(argv=None, **kwargs):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--state", default="docs/hijri.json")
    ap.add_argument("--today", help="YYYY-MM-DD (default: today in Cairo)")
    ap.add_argument("--force", action="store_true", help="check even in mid-month")
    args = ap.parse_args(argv)
    try:
        today = dt.date.fromisoformat(args.today) if args.today else cairo_today()
        run(args.state, today, args.force, **kwargs)
    except (lib.DataError, ValueError) as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
