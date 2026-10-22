import asyncio
import json
import io
from PIL import Image
from decimal import Decimal

import httpx
import pytest
from openai import AsyncOpenAI

from card_platform.config import Settings
from card_platform.image_service import CardVisionService, QwenVisionProvider
from card_platform.vision_routing import TieredVisionRouter
from card_platform import vision_routing
from test_vision_routing import provider, result


def test_defaults_use_flash_for_all_vision_roles_and_separate_search():
    s = Settings(_env_file=None)
    assert s.vision_base_url == 'https://api.deepseek.com'
    assert {s.vision_model, s.vision_plus_model, s.vision_max_model,
            s.vision_finish_confirmation_model} == {'deepseek-flash'}
    assert s.vision_thinking_enabled is False
    s = Settings(_env_file=None, vision_api_key='new-vision', search_api_key='native-search',
                 search_base_url='https://dashscope.aliyuncs.com/compatible-mode/v1')
    service = CardVisionService(s)
    assert str(service.provider.client.base_url) == 'https://api.deepseek.com'
    assert service.search.client.api_key == 'native-search'
    assert str(service.search.client.base_url).rstrip('/') == 'https://dashscope.aliyuncs.com/compatible-mode/v1'
    assert service.tiered_providers['plus'] is None
    assert service.tiered_providers['max'] is None


@pytest.mark.asyncio
async def test_orientation_wire_explicitly_disables_deepseek_default_thinking():
    requests = []
    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={'id':'test', 'object':'chat.completion',
            'created':1, 'model':'deepseek-flash', 'choices':[{'index':0,
            'finish_reason':'stop', 'message':{'role':'assistant',
            'content':json.dumps({'upright_image_index':1,'confidence':0.95})}}]})
    p = QwenVisionProvider('test', 'https://api.deepseek.com', 'deepseek-flash')
    await p.client.close()
    p.client = AsyncOpenAI(api_key='test', base_url='https://api.deepseek.com',
                         http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    try:
        buffer = io.BytesIO()
        Image.new('RGB', (80, 120), 'white').save(buffer, format='JPEG')
        await p.detect_orientation(buffer.getvalue(), 'image/jpeg')
    finally:
        await p.client.close()
    assert requests[0]['model'] == 'deepseek-flash'
    assert requests[0]['thinking'] == {'type':'disabled'}
    assert 'enable_thinking' not in requests[0]


def test_router_records_configured_model_and_conservative_flash_price():
    tier = vision_routing.vision_tier_for_model('flash', 'deepseek-flash')
    router = TieredVisionRouter(provider(result()), None, None,
                               tiers=(tier, tier, tier))
    resolved = asyncio.run(router.recognize())
    assert resolved.recognition_model == 'deepseek-flash'
    assert resolved.recognition_estimated_cost_cny == Decimal('0.028000')
    assert resolved.recognition_route[0]['pricing_basis'] == 'peak_uncached_upper_bound'

@pytest.mark.asyncio
async def test_visual_research_does_not_require_the_separate_search_client():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from card_platform.valuation_worker import AutomatedValuationWorker
    worker = AutomatedValuationWorker.__new__(AutomatedValuationWorker)
    worker.client = None
    worker.image_verifier = SimpleNamespace(client=object())
    worker.vision_service = SimpleNamespace(provider=None)
    worker._local_image_content = AsyncMock(return_value=None)
    worker._local_image_data_url = AsyncMock(return_value=None)
    await worker._research_visual_identity({})
    assert worker._local_image_content.await_count == 2


def test_missing_search_configuration_never_sends_search_key_to_deepseek():
    visual_only = CardVisionService(Settings(_env_file=None, vision_api_key='vision-only'))
    assert visual_only.search.client is None
    paired = CardVisionService(Settings(_env_file=None, vision_api_key='vision',
                                       search_api_key='search'))
    assert paired.search.client is None
    wrong_endpoint = CardVisionService(Settings(_env_file=None, vision_api_key='vision',
        search_api_key='search', search_base_url='https://api.deepseek.com'))
    assert wrong_endpoint.search.client is None


def test_custom_or_disabled_model_does_not_require_a_price_table_entry():
    custom = Settings(_env_file=None, vision_max_enabled=False,
                      vision_max_model='custom-disabled-model')
    injected = object()
    assert CardVisionService(custom, provider=injected).provider is injected
    tier = vision_routing.vision_tier_for_model('flash', 'custom-vision')
    router = TieredVisionRouter(provider(result()), None, None, tiers=(tier,tier,tier))
    resolved = asyncio.run(router.recognize())
    assert resolved.recognition_model == 'custom-vision'
    assert resolved.recognition_estimated_cost_cny is None

@pytest.mark.asyncio
async def test_visual_alias_request_uses_visual_transport_and_disables_thinking():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from card_platform.valuation_worker import AutomatedValuationWorker
    visual_create = AsyncMock(return_value=SimpleNamespace(choices=[SimpleNamespace(
        message=SimpleNamespace(content='{"confidence":0.1}') )]))
    search_create = AsyncMock(side_effect=AssertionError('visual request sent to search'))
    worker = AutomatedValuationWorker.__new__(AutomatedValuationWorker)
    worker.settings = Settings(_env_file=None)
    worker.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=search_create)))
    worker.image_verifier = SimpleNamespace(client=SimpleNamespace(chat=SimpleNamespace(
        completions=SimpleNamespace(create=visual_create))))
    worker.vision_service = SimpleNamespace(provider=None)
    worker._local_image_content = AsyncMock(return_value=None)
    worker._local_image_data_url = AsyncMock(return_value='data:image/jpeg;base64,aA==')
    await worker._research_visual_identity({})
    assert visual_create.await_count == 1
    assert search_create.await_count == 0
    assert visual_create.call_args.kwargs['model'] == 'deepseek-flash'
    assert visual_create.call_args.kwargs['extra_body'] == {'thinking':{'type':'disabled'}}
