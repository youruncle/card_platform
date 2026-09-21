"""Read seller attribute claims without treating explicit negations as positives."""

import re

_AUTOGRAPH = re.compile(
    r"签字|卡签|贴签|签名|亲签|亲笔|簽字|卡簽|貼簽|簽名|親簽|親筆|"
    r"\bauto(?:graph(?:ed)?)?\b|\brpa\b|\b(?:hand[- ]signed|signed)\b",
    re.I,
)
_NON_AUTOGRAPH = re.compile(
    r"(?:印刷|印制|印製|机印|機印|复印|複印|复制|複製|仿制|仿製|印|机|機)"
    r"\s*(?:签名|簽名|签字|簽字|签|簽)|"
    r"(?:无|無|非|没有|沒有|不是|并非|並非)\s*(?:亲笔|親筆|亲签|親簽|签名|簽名|签字|簽字|签|簽)|"
    r"\b(?:printed|pre[- ]printed|facsimile|replica|reproduction|stamped)\s+"
    r"(?:auto(?:graph(?:ed)?)?|signature|signed)\b|"
    r"\b(?:auto(?:graph(?:ed)?)?|signature)\s+(?:is\s+)?(?:printed|facsimile|replica)\b|"
    r"\b(?:no|not|without|non[- ])\s*(?:an?\s+)?(?:auto(?:graph(?:ed)?)?|signature|signed)\b",
    re.I,
)


def title_claims_autograph(title: str) -> bool:
    """A printed/negated signature is not an autograph claim.

    Only the qualified phrase is removed: an independently described actual
    autograph elsewhere still matters, including cards with both kinds.
    """
    return bool(_AUTOGRAPH.search(_NON_AUTOGRAPH.sub(" ", title)))
