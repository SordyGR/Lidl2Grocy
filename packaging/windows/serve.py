"""Waitress entrypoint for the portable zip (needs waitress installed)."""
import os
import sys


def app_dir(base=None):
    """Locate the app package from either layout.

    Zip layout: serve.py sits next to ``Lidl Parser/``. Repo layout:
    serve.py lives in ``packaging/windows/``, two levels below the root.
    """
    here = os.path.dirname(os.path.abspath(__file__ if base is None else base))
    staged = os.path.join(here, "Lidl Parser")
    if os.path.isdir(staged):
        return os.path.normpath(staged)
    return os.path.normpath(os.path.join(here, "..", "..", "Lidl Parser"))


if "--check" in sys.argv[1:]:
    print(app_dir())
    raise SystemExit(0)

sys.path.insert(0, app_dir())
from waitress import serve  # noqa: E402
import app as appmod  # noqa: E402

if __name__ == "__main__":
    serve(appmod.app, host="127.0.0.1", port=int(sys.argv[1]), threads=4)
