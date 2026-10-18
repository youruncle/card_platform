"""Invented typed observations at the model boundary, never real order evidence."""

from card_platform.purchase_cost_images import (
    FieldObservation,
    PhotoObservation,
    PurchaseObservation,
    evaluate_observation,
    observation_binding,
)


def paired_observation(card, line, order_hash):
    fields = [
        FieldObservation(
            kind="card_number",
            value=card.get("card_number") or "Fixture-17",
            text=card.get("card_number") or "Fixture-17",
            origin="printed",
            photo=0,
        ),
        FieldObservation(
            kind="parallel", value="Refractor", text="Refractor", origin="printed", photo=0
        ),
    ]
    if card.get("serial_number") and card.get("serial_total"):
        serial = f"{card['serial_number']}/{card['serial_total']}"
        fields.append(
            FieldObservation(kind="serial", value=serial, text=serial, origin="printed", photo=0)
        )
    obs = PurchaseObservation(
        photos=[PhotoObservation(index=0, role="front"), PhotoObservation(index=1, role="back")],
        target_fields=fields,
        candidate_fields=fields,
        roster_match=True,
        design_match="same",
        finish_match="same",
        front_back_consistent=True,
        finish_basis="printed_mark",
        finish_detail="Both card backs visibly print matching REFRACTOR technology marks.",
    )
    hashes = card["image_content_hashes"]
    targets = [hashes[k] for k in ("front", "back") if hashes.get(k)]
    result = evaluate_observation(obs, card, ["a" * 64, "b" * 64], target_count=len(targets))
    from test_purchase_cost_recall_repair import image_correction

    binding = observation_binding(card, line["item_id"], order_hash, targets, ["a" * 64, "b" * 64])
    confirmation = image_correction()["independent_confirmation"]
    confirmation["evidence_binding"] = binding
    return {
        **result,
        "source": "purchase_multi_image",
        "independent_confirmation": confirmation,
        "confidence": ".90",
        "evidence_binding": observation_binding(
            card, line["item_id"], order_hash, targets, ["a" * 64, "b" * 64]
        ),
        "candidate_image_url": line["image_urls"][0],
    }
