#!/usr/bin/env python3
"""Mail the weekly digest. Driven by its own systemd timer."""

import logging
import sys

from podiumwatch.db import Database
from podiumwatch.notify import send_digest
from podiumwatch.poller import DEFAULT_DB


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    db = Database(DEFAULT_DB)
    try:
        ok = send_digest(db.conn)
    finally:
        db.close()
    print("digest verstuurd" if ok else "digest niet verstuurd (SMTP niet ingesteld)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
