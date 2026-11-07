"""Product photos: link vendors' image attachments to the offers they show."""
from __future__ import annotations

import re

from .db import Database

IMAGE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}


def item_code(filename: str) -> str:
    """'TK3K53CTC_photo.jpg' -> 'tk3k53ctc'"""
    return re.split(r"[_.\- ]", filename)[0].lower()


def offer_images(db: Database, run_id: str) -> dict[str, list[str]]:
    """Photo URLs per offer, from the vendor's email attachments.

    A photo belongs to an offer when its file name starts with an item code (e.g. TK3K53CTC_photo.jpg)
    that appears in the offer's product name, product ref or notes. If a vendor thread has exactly one
    offer and one photo, they pair up. Otherwise an offer without a matching photo gets none.
    """
    out: dict[str, list[str]] = {}
    offers = db.offers_for_run(run_id)
    for thread_id in {o["thread_id"] for o in offers}:
        photos = [a for e in db.emails_for_thread(thread_id) if e["direction"] == "in"
                  for a in e["attachments"] if a["content_type"] in IMAGE_TYPES]
        mine = [o for o in offers if o["thread_id"] == thread_id]
        for o in mine:
            text = f"{o.get('product_name', '')} {o.get('product_ref', '')} {o.get('notes', '')}".lower()
            hits = [a for a in photos if len(code := item_code(a["filename"])) >= 4 and code in text]
            if not hits and len(mine) == 1 and len(photos) == 1:
                hits = photos
            if hits:
                out[o["id"]] = [f"/runs/{run_id}/attachments/{a['id']}" for a in hits]
    return out
