#!/usr/bin/env python3
"""Local dev server. On the Pi this runs under gunicorn instead.

The reloader is off: watchdog crashes on some Python 3.13 builds, and this
app is small enough that restarting by hand costs nothing.
"""

from podiumwatch.web import app

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5011, debug=True, use_reloader=False)
