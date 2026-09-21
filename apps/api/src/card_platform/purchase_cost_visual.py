"""Reuse version- and asset-bound identity reviews; never reuse market prices."""

import re

from card_platform.comparable_image_verifier import VisualCandidate, reviewed_visual_decision
from card_platform.domain.card_identity import normalize_text


def same_reviewed_identity(card, identity):
    fields = (
        "player_name",
        "brand_name",
        "season_start",
        "season_end",
        "product_name",
        "set_name",
        "card_number",
        "insert_name",
        "parallel_name",
        "serial_number",
        "grade_company",
        "grade_value",
        "is_rookie",
        "is_autograph",
        "is_memorabilia",
        "is_patch",
    )
    for field in fields:
        before = identity.get(field, False if field.startswith("is_") else None)
        after = card.get(field, False if field.startswith("is_") else None)
        if normalize_text(before) != normalize_text(after):
            return False
    return (card.get("serial_total") or card.get("print_run")) == identity.get("serial_total")


async def load_purchase_visual_reviews(conn, owner, item_id, card):
    result = await conn.execute(
        """SELECT v.id,v.identity FROM card_identity_version v
           JOIN collection_item ci ON ci.id=v.collection_item_id
           WHERE ci.id=%s AND ci.owner_id=%s AND v.review_status='confirmed'
             AND NOT EXISTS (SELECT 1 FROM card_identity_version revoked
                             WHERE revoked.supersedes_id=v.id AND revoked.review_status='rejected')
           ORDER BY v.version DESC LIMIT 1""",
        (item_id, owner),
    )
    version = await result.fetchone()
    if not version or not same_reviewed_identity(card, version["identity"]):
        return []
    result = await conn.execute(
        """SELECT d.id,d.identity_version_id,d.decision,e.source_url,
                  d.classification->'visual_review' AS review
           FROM market_evidence_identity_decision d
           JOIN market_evidence e ON e.id=d.market_evidence_id
           WHERE d.collection_item_id=%s AND d.identity_version_id=%s
             AND d.classification ? 'visual_review'""",
        (item_id, version["id"]),
    )
    return await result.fetchall()


def _item_id(url):
    match = re.fullmatch(
        r"https://(?:www\.)?cardhobby\.com\.cn/market/item/(\d{1,40})/?", str(url or ""), re.I
    )
    return match[1] if match else None


def purchase_visual_evidence(card, line, reviews):
    """Case-insensitive CardHobby route identity; all image assets remain exact."""
    matches = []
    for row in reviews:
        source = row["source_url"]
        if not _item_id(source) or _item_id(source) != _item_id(line.get("item_url")):
            continue
        review = row["review"] or {}
        if review.get("candidate_image_url") not in line.get("image_urls", []):
            continue
        decision = reviewed_visual_decision(
            card,
            VisualCandidate(source, line["title"], review["candidate_image_url"]),
            review,
        )
        expected = {"accepted": "same", "rejected": "different"}.get(row["decision"])
        if decision and decision.verdict == expected:
            matches.append(
                {
                    **review,
                    "decision_id": str(row["id"]),
                    "identity_version_id": str(row["identity_version_id"]),
                }
            )
    # A conflicting paired-image rejection is not outvoted by another review.
    return next(
        (r for r in matches if r["verdict"] == "different"), matches[0] if matches else None
    )
