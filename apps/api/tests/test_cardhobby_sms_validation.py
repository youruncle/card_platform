import asyncio
from types import SimpleNamespace

import pytest
from card_platform import cardhobby_account_api as api


@pytest.mark.parametrize('code', ['7215', '0123', '123456'])
def test_sms_code_reaches_account_service(monkeypatch, code):
    calls = []

    async def login(conn, owner, sms_code):
        calls.append((owner, sms_code))
        return {'saved': True, 'state': 'connected'}

    async def body():
        return {'code': code}

    monkeypatch.setattr(api, 'account_service', lambda: SimpleNamespace(login=login))
    result = asyncio.run(api.verify(SimpleNamespace(json=body), SimpleNamespace(id='owner'), None))
    assert result.status_code == 200
    assert calls == [('owner', code)]


@pytest.mark.parametrize('code', ['', '123', '1234567', '12a4', '１２３４', 7215])
def test_invalid_sms_code_is_rejected(code):
    async def body():
        return {'code': code}

    result = asyncio.run(api.verify(SimpleNamespace(json=body), SimpleNamespace(id='owner'), None))
    assert result.status_code == 422
