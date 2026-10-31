from pathlib import Path

from card_platform.config import Settings

ROOT=Path(__file__).resolve().parents[3]


def test_deployment_preserves_production_workers_and_separate_visual_search():
    from card_platform.mcp.deployment import check_runtime
    settings=Settings(_env_file=None,vision_api_key='test-vision',search_api_key='test-search',
        search_base_url='https://dashscope.aliyuncs.com/compatible-mode/v1')
    result=check_runtime(settings)
    assert result['purchase_cost_worker_enabled'] is True
    assert result['vision_thinking_disabled'] is True
    assert result['search_credentials_separate'] is True
    assert result['oauth_redirects_configured'] is False
    assert 'test-vision' not in str(result) and 'test-search' not in str(result)
    bad=settings.model_copy(update={'purchase_cost_worker_enabled':False})
    assert check_runtime(bad)['purchase_cost_worker_enabled'] is False


def test_frontend_proxies_mcp_and_metadata_and_compose_passes_explicit_oauth_configuration():
    config=(ROOT/'apps/web/next.config.ts').read_text()
    assert 'source: "/mcp"' in config
    assert 'source: "/.well-known/:path*"' in config
    for filename in ('compose.prod.yaml','compose.tunnel.yaml'):
        compose=(ROOT/'infra'/filename).read_text()
        assert 'CHATGPT_MCP_RESOURCE_URL' in compose
        assert 'CHATGPT_OAUTH_REDIRECT_URIS' in compose
        assert "PURCHASE_COST_WORKER_ENABLED: 'true'" in compose
