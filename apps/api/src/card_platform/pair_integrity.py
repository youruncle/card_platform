"""Server-owned, source-bound semantic gate for paired card ingestion."""
import re
import unicodedata

from fastapi import HTTPException

from card_platform.schemas import CardPairValidation


def _text(value):
    return re.sub(r"[^\w]+", " ", str(value or "").casefold()).strip()


def _product(value):
    value = _text(value)
    # Manufacturer/technology marks on the same card are not distinct products.
    value = re.sub(r"\b(topps|bowman|panini|basketball|nba)\b", "", value)
    value = re.sub(r"\b(upper deck|collector s edge|collectors edge|fleer|skybox)\b", "", value)
    value = " ".join(value.split())
    return {"donruss optic": "optic", "topps now": "now"}.get(value, value)


def _brand(value):
    value = re.sub(r"[\W_]+", "", _text(value))
    return {"bowman": "topps", "toppsbowman": "topps", "paniniamerica": "panini"}.get(value, value)


def _card_number(value):
    value = str(value or "").strip()
    # Strip printed labels only; catalog letter prefixes and zeroes are identity.
    value = re.sub(r"^(?:no(?:\.\s*|\s+)|n[º°]\s*|#\s*)", "", value, flags=re.I)
    return _text(value).replace(" ", "")


def _subject(value):
    value = unicodedata.normalize("NFKD", str(value or ""))
    return _text("".join(char for char in value if not unicodedata.combining(char)))


def _credible_product(value):
    return bool(re.search(
        r"\b(prizm|select|instant|mosaic|optic|chrome|finest|inception|status|impulse|"
        r"hoops|now|photogenic|revolution|obsidian|noir|recon|threads|origins|absolute|"
        r"donruss|flawless|immaculate|signature class|national treasures)\b", value))


def _subject_match(front, back):
    # Missing middle names, suffixes or initials are compatible but not a full match.
    return any(len(name.split()) >= 2 and all(len(part) > 1 for part in name.split())
               for name in front & back)


def _partial_subject_match(front, back):
    suffixes = {"jr", "sr", "ii", "iii", "iv", "v"}

    def compatible(left, right):
        return left == right or (min(len(left), len(right)) == 1 and left[0] == right[0])

    for left in front:
        for right in back:
            a, b = left.split(), right.split()
            suffix_a = a.pop() if a[-1] in suffixes else None
            suffix_b = b.pop() if b[-1] in suffixes else None
            if not a or not b or (suffix_a and suffix_b and suffix_a != suffix_b):
                continue
            if a[-1] != b[-1]:
                continue
            if len(a) == 1 or len(b) == 1:
                return True
            if not compatible(a[0], b[0]):
                continue
            middle_a, middle_b = a[1:-1], b[1:-1]
            if (middle_a and middle_b and
                    (len(middle_a) != len(middle_b) or
                     not all(compatible(x, y) for x, y in zip(middle_a, middle_b)))):
                continue
            return True
    return False


def _manufacturer_product_layers(job, front, back, brands, subjects, inserts, numbers):
    """Recognize printed manufacturer/product layers only on an anchored pair."""
    def product_label(value):
        text = re.sub(r"\b(?:19|20)\d{2}\s+\d{2}\b", "", _text(value))
        return " ".join(re.sub(r"\b(nba|basketball|upper deck)\b", "", text).split())

    products = [product_label(face["product_name"]) for face in (front, back)]
    for index, face in enumerate((front, back)):
        if (not products[index] and brands[index] == "skybox"
                and re.search(r"\bskybox\b", _text(" ".join(face["evidence"])))):
            # A same-face printed logo is only a comparison anchor, not an
            # invented product observation or a general manufacturer alias.
            products[index] = "skybox"
    printed = _text(" ".join((job.get("field_evidence") or {}).get("brand_name") or []))
    anchored = (
        set(brands) == {"skybox", "upperdeck"}
        and products[0] == products[1] and "skybox" in products[0].split()
        and len(subjects[0]) == 1 and subjects[0] == subjects[1]
        and _subject_match(*subjects)
        and ((all(inserts) and inserts[0] == inserts[1])
             or (all(numbers) and numbers[0] == numbers[1]))
        and front["evidence"] and back["evidence"]
        and re.search(r"\bskybox\b", printed) and re.search(r"\bupper deck\b", printed)
    )
    if not anchored:
        return False, False
    observed_numbers = set(numbers) - {""}
    observed_inserts = set(inserts) - {""}
    subject, brand = _subject(job.get("player_name")), _brand(job.get("brand_name"))
    product, number = product_label(job.get("product_name")), _card_number(job.get("card_number"))
    insert = _text(job.get("insert_name"))
    conflict = bool(
        (subject and subject not in subjects[0]) or (brand and brand not in brands)
        or (product and product != products[0])
        or (number and observed_numbers and number not in observed_numbers)
        or (insert and observed_inserts and insert not in observed_inserts)
    )
    supported = (subject and brand and product
                 and (not observed_numbers or number in observed_numbers)
                 and (not observed_inserts or insert in observed_inserts))
    return bool(supported and not conflict), conflict


def _verified_dual_design(products, numbers, subjects, inserts):
    """Narrow catalog exceptions verified from preserved original cards, not a model flag.

    A front-like reverse can only downgrade to explicit review. It cannot make
    incompatible products, printed numbers or duplicate image bytes acceptable.
    """
    if not all(products) or products[0] != products[1]:
        return False
    people = set.union(*subjects)
    printed = set(numbers) - {""}
    return (("impulse" in products[0] and printed == {"61"}
             and people == {"kobe bryant", "corey benjamin"})
            or ("generations" in [products[0], *inserts] and printed == {"g11"}
                and people == {"vince carter", "oscar robertson"})
            or (products[0].replace(" ", "") == "centurylegends"
                and inserts == ["generations", "generations"] and not printed
                and people == {"vince carter", "oscar robertson"}))


def _caveat_conflicts(caveats):
    found = []
    for raw in caveats or []:
        text = str(raw)
        # Ignore explicit negative findings and uncertainty; these are not proof.
        if re.search(r"未发现|没有.*冲突|无.*冲突|not? (?:a )?mismatch|no .*conflict", text, re.I):
            continue
        if re.search(r"不是同一张卡|并非同一张|正反面不匹配|different cards|pair.*mismatch|front.*back.*(?:conflict|mismatch)|两张.*正面|both.*front", text, re.I):
            found.append(text)
            continue
        sides = re.search(r"(?:正面|front)[：:\s]*(.+?)(?:背面|back)[：:\s]*(.+)", text, re.I)
        if sides:
            def names(part):
                return {_product(m.group()) for m in re.finditer(
                    r"Donruss Optic|Prizm|Select|Instant|Mosaic|Finest|Inception|(?:Topps |Bowman )?Chrome|Status|Topps Now", part, re.I)}
            front, back = names(sides[1]), names(sides[2])
            if len(front) == len(back) == 1 and front != back:
                found.append(text)
    return found


def derive_pair_validation(job_result: dict, image_hashes: dict) -> dict:
    """Bind current bytes and independently check observations, never a status alone."""
    raw = job_result.get("pair_validation")
    if hasattr(raw, "model_dump"):
        raw = raw.model_dump(mode="json")
    try:
        pair = CardPairValidation.model_validate(raw or {}).model_dump(mode="json")
    except (ValueError, TypeError):
        pair = CardPairValidation(
            status="conflict" if isinstance(raw, dict) and raw.get("status") == "conflict" else "unknown",
            reason_codes=["invalid_pair_validation"],
        ).model_dump(mode="json")
    front, back = pair["front"], pair["back"]
    reasons, evidence = [], list(pair["evidence"])
    if image_hashes.get("front") and image_hashes.get("front") == image_hashes.get("back"):
        reasons.append("identical_images")
    if front["role"] == back["role"] == "front":
        reasons.append("duplicate_front")
    if front["role"] == back["role"] == "back":
        reasons.append("duplicate_back")
    products = [_product(face["product_name"]) for face in (front, back)]
    brands = [_brand(face["brand_name"]) for face in (front, back)]
    if all(map(_credible_product, products)) and products[0] != products[1]:
        reasons.append("product_conflict")
    known_brands = {"panini", "topps", "upperdeck", "collectorsedge", "fleer", "skybox", "leaf", "daka", "onyx", "sage"}
    if all(brand in known_brands for brand in brands) and brands[0] != brands[1]:
        reasons.append("brand_conflict")
    numbers = [_card_number(face["card_number"]) for face in (front, back)]
    if all(numbers) and numbers[0] != numbers[1]:
        reasons.append("card_number_conflict")
    inserts = [_text(face["insert_name"]) for face in (front, back)]
    if all(inserts) and inserts[0] != inserts[1]:
        reasons.append("insert_conflict")
    subjects = [{_subject(name) for name in face["player_names"] if _subject(name)} for face in (front, back)]
    verified_dual = _verified_dual_design(products, numbers, subjects, inserts)
    if verified_dual:
        reasons = [reason for reason in reasons if reason not in {"duplicate_front", "duplicate_back"}]
    shared_product = bool(all(products) and products[0] == products[1])
    shared_number = bool(all(numbers) and numbers[0] == numbers[1])
    shared_insert = bool(all(inserts) and inserts[0] == inserts[1])
    multi_design = (shared_product and shared_number and
                    ((shared_insert and inserts[0] in {"generations", "impulse"})
                     or "impulse" in products[0].split()
                     or front["multi_subject"] or back["multi_subject"]))
    shared_subject = _subject_match(*subjects)
    manufacturer_layers, joint_conflict = _manufacturer_product_layers(
        job_result, front, back, brands, subjects, inserts, numbers,
    )
    if manufacturer_layers:
        reasons = [reason for reason in reasons if reason != "brand_conflict"]
    if joint_conflict:
        reasons.append("draft_identity_conflict")
    partial_subject = _partial_subject_match(*subjects)
    if all(subjects) and not shared_subject and not partial_subject and not multi_design and not verified_dual:
        reasons.append("subject_conflict")
    caveats = _caveat_conflicts(job_result.get("caveats"))
    if caveats and not verified_dual:
        reasons.append("reported_face_conflict")
        evidence.extend(caveats)
    if pair["status"] == "conflict" and not verified_dual:
        model_reasons = pair["reason_codes"] or ["model_reported_conflict"]
        if manufacturer_layers:
            model_reasons = [reason for reason in model_reasons if reason not in {
                "brand_conflict", "draft_identity_conflict", "shared_player_and_insert", "matching_design",
            }]
        reasons.extend(model_reasons)
    if reasons:
        status = "conflict"
    elif verified_dual:
        status = "unknown"
        reasons.append("verified_multi_subject_design_requires_review")
        evidence.append("该编号属于已核对的双主体版式，请查看当前两面后确认")
    elif manufacturer_layers:
        status = "unknown"
        reasons.append("manufacturer_product_layers_observed")
    elif (raw and pair["status"] == "consistent"
          and front["role"] == "front" and back["role"] == "back"
          and front["evidence"] and back["evidence"]
          and ((shared_product and (shared_subject or shared_number))
               or (any(products) and not all(products) and shared_subject and shared_number))):
        status = "consistent"
    else:
        status = "unknown"
        reasons.append("independent_face_evidence_missing" if not raw else "pair_not_verified")
    return {**pair, "status": status, "reason_codes": list(dict.fromkeys(reasons)),
            "evidence": list(dict.fromkeys(evidence)), "image_content_hashes": dict(image_hashes), "version": 1}


def validate_pair_for_confirmation(job_result: dict, image_hashes: dict, review: dict | None) -> None:
    """Uncertain identity may be saved pending review; conflicts cannot pass."""
    if set(image_hashes) != {"front", "back"}:
        return  # Explicit single-image capture retains its previous behavior.
    if not all(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value)
               for value in image_hashes.values()):
        raise HTTPException(409, "无法验证当前双面图片，请重新上传")
    raw = job_result.get("pair_validation") or {}
    saved_hashes = raw.get("image_content_hashes") if isinstance(raw, dict) else None
    if saved_hashes and saved_hashes != image_hashes:
        raise HTTPException(409, "图片已更换，原配对审核已失效，请重新识别")
    if review is not None and not (
        isinstance(review, dict) and review.get("confirmed") is True
        and review.get("image_content_hashes") == image_hashes
    ):
        raise HTTPException(409, "配对审核与当前照片不一致，请刷新后重试")
    decision = derive_pair_validation(job_result, image_hashes)
    if decision["status"] == "conflict":
        raise HTTPException(409, "正反面存在明确冲突，请重新配对或补拍：" + ", ".join(decision["reason_codes"]))
    if decision["status"] == "consistent" and saved_hashes == image_hashes:
        return
    if (isinstance(review, dict) and review.get("confirmed") is True
            and review.get("image_content_hashes") == image_hashes):
        return
    return  # Missing evidence is a pending marker, never an intake prerequisite.


def retained_recognition_result(row: dict) -> dict | None:
    """Inconclusive retries cannot erase earlier source-bound hard conflicts."""
    current = row.get('result')
    previous = (row.get('capture_metadata') or {}).get('last_recognition_result')
    if not previous:
        return current
    if not current:
        return previous
    old_pair = derive_pair_validation(previous, (previous.get('pair_validation') or {}).get('image_content_hashes', {}))
    new_pair = derive_pair_validation(current, (current.get('pair_validation') or {}).get('image_content_hashes', {}))
    if old_pair['status'] == 'conflict' and new_pair['status'] != 'consistent':
        return {**current, 'pair_validation': old_pair}
    return current
