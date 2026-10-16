"""Original-art cards: depicted subject, set logo and artist are distinct roles."""

import re

SKETCH_ALIASES = (
    "artist sketch card", "art sketch card", "sketch card", "sketch cards",
    "手绘卡", "手繪卡", "原画卡",
)
_SKETCH = re.compile(r"\b(?:artist\s+|art\s+)?sketch\s+cards?\b|手[绘繪]卡|原画卡", re.I)
_ARTIST_SIGNATURE = re.compile(
    r"(?:画师|畫師|画家|繪師|绘师|作者)\s*(?:亲笔|親筆)?\s*"
    r"(?:(?:亲签|親簽|签字|签名|簽名|卡签|卡簽|签绘)\s*)+|"
    r"\bartist\s+(?:(?:hand|on[ -]card)\s+)?(?:signed|signature|autograph(?:ed)?)\b|"
    r"\b(?:signed|autographed)\s+by\s+(?:the\s+)?artist\b", re.I,
)
SKETCH_RECOGNITION_INSTRUCTIONS = (
    "手绘卡/Artist Sketch Card 必须区分正面所绘角色、系列 Logo 和画师："
    "player_name 是正面作品主体，不能用系列 Logo、通用卡背人物或 ARTIST 署名替代。"
    "通用卡背仅支持系列、年份、编号与画师信息，不能覆盖正面角色。"
    "无法确认作品主体时 player_name 返回 null 并明确待核对，不能因 Logo 清晰给高置信度。"
    "画师在手绘作品上的署名记录到 field_evidence/caveats，不等于球员/演员签字卡，"
    "不得仅凭画师署名设置 is_autograph=true。手绘原作的 1 of 1 不表示同角色所有作品相同。"
)


def is_sketch_card(value: object) -> bool:
    return bool(_SKETCH.search(str(value or "")))


def without_artist_signature(title: str) -> str:
    """Strip only explicitly attributed artist signatures, never bare auto claims."""
    return _ARTIST_SIGNATURE.sub(" ", title)
