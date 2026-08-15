from card_platform.auth import (
    client_metadata,
    hash_session_token,
    normalize_email,
    password_hasher,
    password_matches,
)
from starlette.requests import Request


def test_email_normalization_is_case_insensitive() -> None:
    assert normalize_email("  Song@Example.COM ") == "song@example.com"


def test_invalid_email_is_rejected() -> None:
    try:
        normalize_email("not-an-email")
    except ValueError as error:
        assert "有效" in str(error)
    else:
        raise AssertionError("invalid email was accepted")


def test_password_hash_does_not_store_plaintext() -> None:
    password = "A-long-test-password!"
    password_hash = password_hasher.hash(password)
    assert password not in password_hash
    assert password_matches(password_hash, password)
    assert not password_matches(password_hash, "wrong-password")


def test_session_token_hash_is_stable_and_non_plaintext() -> None:
    token = "private-session-token"
    assert hash_session_token(token) == hash_session_token(token)
    assert hash_session_token(token) != token
    assert len(hash_session_token(token)) == 64


def test_non_ip_test_client_address_is_not_written_to_inet_column() -> None:
    request = Request({"type": "http", "client": ("testclient", 50000), "headers": []})
    address, _ = client_metadata(request)
    assert address is None
