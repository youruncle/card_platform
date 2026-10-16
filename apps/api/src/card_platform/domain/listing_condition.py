"""Condition disclosure and current paired-image admission, shared by all gates."""
import json
import re
from dataclasses import replace
from decimal import Decimal

def title_discloses_damage(title: str) -> bool:
    location = r"(?:卡面|表面|正面|背面|卡[边邊]|[边邊](?:沿|缘|緣|角|框)?|(?:左|右)?(?:上|下)?角)"
    severity = r"(?:细微|細微|轻微|輕微|明显|明顯|微|小|轻|輕|略)?"
    localized_damage = location + r"\s*(?:有|处|處)?\s*" + severity + r"\s*(?:翻(?:起|[边邊])?|[压壓](?:伤|傷|痕)?)(?!纹|紋|设计|設計)"
    explicit_damage = r"[压壓][伤傷]|[开開]裂|破[损損]|卡[损損]"
    terms = (
        localized_damage + "|" + explicit_damage + "|"
        r"(?:微|小|轻微|輕微)?瑕(?:疵)?|有伤|有傷|折痕|磕角|压痕|壓痕|轻印|輕印|印痕|凹痕|划痕|劃痕|白角"
        r"|印刷线|印刷線|刮伤|刮傷|磨损|磨損|角\s*(?:小|轻微|輕微)?\s*折"
        r"|(?:微|轻|輕|细|細|印刷)痕|痕\s*如图|痕\s*如圖"
        r"|(?:轻微|輕微|微|有|小|明显|明顯)\s*白[边邊点點]"
        r"|(?:边角|邊角|边|邊|角)\s*(?:微|轻微|輕微)白"
        r"|(?:边角|邊角|边|邊|角)\s*(?:细微|細微|轻微|輕微)?(?:小)?[翘翹]"
        r"|(?:轻微|輕微|微|小)?(?:翘脚|翹腳)"
    )
    # Printed design names and holder defects are not statements about the card.
    # Strip only the local holder phrase, preserving any later card disclosure.
    title = re.sub(r"(?:" + explicit_damage + r")\s*(?:图案|圖案|纹样|紋樣|设计|設計|图示说明|圖示說明)", " ", title)
    holder = r"(?:卡套|卡砖|卡磚|保护壳|保護殼|评级壳|評級殼|卡具|卡壳|卡殼|(?<![\u3400-\u9fff])[壳殼])"
    title = re.sub(
        holder + r"\s*(?:的)?\s*(?:" + location + r")?\s*(?:有)?\s*" + severity
        + r"\s*(?:" + terms + r")", " ", title,
    )
    # A spot disclosure is contextual: printed dotted artwork is not damage.
    spots = re.sub(r'(?:无|無|没有|沒有|未见|未見)\s*(?:明显|明顯)?\s*(?:小)?花[点點]', '', title)
    spots = re.sub(r'花[点點]\s*(?:图案|圖案|设计|設計|纹样|紋樣)', '', spots)
    if re.search(r'(?:边框|邊框|正面|背面|表面).{0,8}(?:小)?花[点點]', spots):
        return True
    text = re.sub(r"(?:无|無|没有|沒有|未见|未見)(?:明显|明顯)?\s*(?:" + terms + r"|" + severity + r"(?:翻(?:起|[边邊])?|[压壓]))", " ", title)
    text = re.sub(r"\b(?:no|without|not)\s+(?:visible\s+)?(?:damage[d]?|crease[sd]?)\b", " ", text, flags=re.I)
    return bool(re.search(terms + r"|\bdamaged\b|\bcrease[sd]?\b", text, re.I))



def title_disclaims_card_condition(title: str) -> bool:
    """An explicit card-condition guarantee denial, not generic seller boilerplate."""
    condition = r"(?:卡品|品相|品(?!牌|种|種|类|類|质|質))"
    denial = r"不\s*(?:保[证證]|保证|包|保)"
    claim = rf"(?:{denial}\s*{condition}|{condition}\s*{denial})"
    holder = r"(?:卡套|卡砖|卡磚|保护壳|保護殼|评级壳|評級殼|卡具|卡壳|卡殼|(?<![\u3400-\u9fff])[壳殼])"
    # Exclude only an immediately identified holder, never a whole sentence
    # that could also contain a separate disclosure about the card itself.
    text = re.sub(rf"{holder}\s*(?:的)?\s*{claim}", " ", title)
    text = re.sub(rf"(?:不是|并非|並非)\s*{claim}", " ", text)
    return bool(re.search(claim, text))



# Only explicit minor disclosure is eligible for a comparison. Structural or
# unspecified damage remaining in the title keeps its original hard gate.
_MINOR = r"(?:轻微|輕微|微|小)瑕(?:疵)?|轻印|輕印|(?:细微|細微|轻微|輕微|微)痕"


def title_discloses_minor_condition(title: str) -> bool:
    return (title_discloses_damage(title) and bool(re.search(_MINOR, title))
            and not title_discloses_damage(re.sub(_MINOR, ' | ', title)))


def condition_binding_matches(observed, current):
    if not isinstance(observed, dict) or not isinstance(current, dict) or not current:
        return False
    metadata = {'model', 'model_observation_contract'}
    return ({k: v for k, v in observed.items() if k not in metadata}
            == {k: v for k, v in current.items() if k not in metadata})


def condition_review_is_current(review, binding) -> bool:
    return (isinstance(review, dict) and isinstance(binding, dict) and bool(binding)
        and condition_binding_matches(review.get('evidence_binding'), binding)
        and review.get('source') == 'images' and review.get('scope') == 'visible_front'
        and review.get('verdict') in {'comparable_minor', 'materially_different', 'uncertain'}
        and all(isinstance(review.get(key), str) and len(review[key].strip()) >= 12
                for key in ('target_observation', 'candidate_observation')))


def minor_condition_is_comparable(identity, title, facts, *, source_url=None, image_url=None):
    if identity.get('grade_company') or facts is None:
        return False
    if not facts.current_for(identity, source_url or facts.candidate_id, title, image_url or facts.image_url):
        return False
    raw = json.loads(facts.raw_json)
    if (facts._reviewed_proof is not None
            and not condition_binding_matches(raw.get('visual_evidence_binding'), json.loads(facts._reviewed_proof.binding_json))):
        return False
    review = raw.get('visual_condition_review')
    return (condition_review_is_current(review, raw.get('visual_evidence_binding'))
        and review['verdict'] == 'comparable_minor'
        and review.get('target_front_complete') is True
        and review.get('candidate_front_complete') is True)


def condition_rejection_reasons(identity, title, facts=None, *, discovery=False, source_url=None, image_url=None):
    reasons = []
    if title_discloses_damage(title):
        if not title_discloses_minor_condition(title):
            reasons.append('disclosed_condition_damage_requires_separate_valuation')
        elif not discovery and not minor_condition_is_comparable(
                identity, title, facts, source_url=source_url, image_url=image_url):
            reasons.append('minor_condition_requires_current_image_comparison')
    if title_disclaims_card_condition(title):
        reasons.append('listing_condition_not_guaranteed')
    return reasons


def disclose_minor_condition(classification, title):
    if not title_discloses_minor_condition(title):
        return classification
    # No invented mint-price uplift or fixed damage discount. Preserve the real
    # sale amount, cap its evidence weight and expose the limited review scope.
    return replace(classification, source_reliability=min(classification.source_reliability, Decimal('.55')),
        field_matches={**classification.field_matches, 'minor_condition_disclosed': True,
            'condition_basis': 'visible_front_minor_comparable'})
