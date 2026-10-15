import logging
import os
from threading import Thread

from flask import Flask

app = Flask(__name__)
logging.getLogger("werkzeug").setLevel(logging.ERROR)  # keep logs clean from pings


@app.route("/")
def home():
    return "Bot is alive"


@app.route("/health")
def health():
    return "ok"


def run():
    # Render gives the port in the PORT env var
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))


def keep_alive():
    Thread(target=run, daemon=True).start()
