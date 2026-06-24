"""Dev launcher: starts Flask and opens the browser automatically."""

import threading
import webbrowser

from app import app

PORT = 5050
URL  = f"http://127.0.0.1:{PORT}"


def _open_browser():
    webbrowser.open(URL)


if __name__ == "__main__":
    threading.Timer(1.2, _open_browser).start()
    app.run(host="127.0.0.1", port=PORT, debug=False)
