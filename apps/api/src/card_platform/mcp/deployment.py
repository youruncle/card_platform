"""Credential-free assertions shared by release verification and tests."""
from urllib.parse import urlsplit

from card_platform.model_requests import nonthinking_extra_body


def check_runtime(settings):
    visual_models=[settings.vision_model,settings.vision_plus_model,settings.vision_max_model,settings.vision_finish_confirmation_model]
    search=urlsplit(settings.resolved_search_base_url)
    host=search.hostname or ''
    search_host=(host=='dashscope.aliyuncs.com' or host.endswith('.dashscope.aliyuncs.com') or host.endswith('.maas.aliyuncs.com'))
    visual_key=settings.vision_api_key or settings.ai_api_key
    redirects=settings.chatgpt_oauth_redirect_uris
    return {
        'purchase_cost_worker_enabled':settings.purchase_cost_worker_enabled is True,
        'vision_models_deepseek_flash':all(model=='deepseek-flash' for model in visual_models),
        'vision_endpoint_deepseek':urlsplit(settings.vision_base_url).hostname=='api.deepseek.com',
        'vision_thinking_disabled':settings.vision_thinking_enabled is False and all(nonthinking_extra_body(model)=={'thinking':{'type':'disabled'}} for model in visual_models),
        'search_credentials_separate':bool(settings.search_api_key and settings.search_api_key!=visual_key),
        'search_endpoint_bailian':search.scheme=='https' and search_host,
        'search_models_qwen_plus':settings.card_search_model==settings.valuation_search_model=='qwen-plus',
        'oauth_canonical_urls':settings.chatgpt_mcp_resource_url=='https://cardsp.cn/mcp' and settings.chatgpt_oauth_issuer_url=='https://cardsp.cn/v1/chatgpt/oauth' and settings.chatgpt_web_origin=='https://cardsp.cn',
        'oauth_redirects_configured':bool(redirects) and all(urlsplit(url).scheme=='https' and not any(character in url for character in ('*','#')) for url in redirects),
    }
