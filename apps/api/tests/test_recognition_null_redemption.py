import pytest
from pydantic import ValidationError

from card_platform.schemas import CardRecognitionResult


def test_null_redemption_status_preserves_recognition_as_unknown():
    result = CardRecognitionResult.model_validate_json(
        '{"player_name":"Stephen Curry","confidence":0.86,'
        '"is_redemption":false,"redemption_status":null}'
    )
    assert result.player_name == "Stephen Curry"
    assert result.is_redemption is False
    assert result.redemption_status == "unknown"


@pytest.mark.parametrize("status", ["unknown", "unredeemed", "redeemed", "expired"])
def test_explicit_redemption_status_is_preserved(status):
    result = CardRecognitionResult.model_validate(
        {"confidence": 0.86, "redemption_status": status}
    )
    assert result.redemption_status == status


def test_invalid_redemption_status_is_rejected():
    with pytest.raises(ValidationError):
        CardRecognitionResult.model_validate(
            {"confidence": 0.86, "redemption_status": "unsupported"}
        )
