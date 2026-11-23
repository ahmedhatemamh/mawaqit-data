"""Daily job: update docs/hijri.json from Dar al-Ifta, only around Hijri month boundaries.

Exit codes: 0 = ok (changed or nothing to do), 2 = failure (nothing was written).
"""
import argparse
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hijri_lib as lib  # noqa: E402


def cairo_today():
    from zoneinfo import ZoneInfo
    return dt.datetime.now(ZoneInfo("Africa/Cairo")).date()


def run(state_path, today, force=False, fetch=lib.fetch_page):
    state = lib.load_state(state_path)
    need, reason = lib.should_fetch(state, today)
    if not need and not force:
        print("skip: " + reason)
        return False
    print("check: " + ("forced" if force and not need else reason))
    obs = lib.parse_page(fetch())
    changed = lib.apply_observation(state, obs, today)
    if changed:
        lib.save_state(state_path, state)
    print("%s: %r" % ("updated" if changed else "unchanged", obs))
    return changed


def main(argv=None, fetch=lib.fetch_page):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--state", default="docs/hijri.json")
    ap.add_argument("--today", help="YYYY-MM-DD (default: today in Cairo)")
    ap.add_argument("--force", action="store_true", help="check the site even in mid-month")
    args = ap.parse_args(argv)
    try:
        today = dt.date.fromisoformat(args.today) if args.today else cairo_today()
        run(args.state, today, args.force, fetch)
    except (lib.DataError, ValueError) as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
