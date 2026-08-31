#!/usr/bin/env python3
"""Poll every venue once and print what happened. Safe to run by hand."""

import argparse
import json
import logging
import sys

from podiumwatch.poller import poll_once


def main() -> int:
    ap = argparse.ArgumentParser(description="Poll venues once.")
    ap.add_argument("--venue", help="only poll this venue id")
    ap.add_argument("--db", help="database path")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    kwargs = {"only": args.venue}
    if args.db:
        kwargs["db_path"] = args.db
    summary = poll_once(**kwargs)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
