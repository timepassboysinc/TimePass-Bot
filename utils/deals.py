"""Tiny JSON store for auto-MM deals, keyed by ticket channel id."""
import json
import os

DEALS_FILE = "data/deals.json"


def _load():
    os.makedirs("data", exist_ok=True)
    if not os.path.exists(DEALS_FILE):
        return {}
    with open(DEALS_FILE, "r") as f:
        return json.load(f)


def _save(data):
    os.makedirs("data", exist_ok=True)
    tmp = DEALS_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, DEALS_FILE)  # atomic, so a crash can't corrupt the file


def get(channel_id):
    return _load().get(str(channel_id))


def save(deal):
    data = _load()
    data[str(deal["channel_id"])] = deal
    _save(data)


def delete(channel_id):
    data = _load()
    data.pop(str(channel_id), None)
    _save(data)


def all_deals():
    return list(_load().values())
