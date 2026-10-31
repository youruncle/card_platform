"""Portable plugin validation against the published Agent Plugins schemas."""
import json
from pathlib import Path
from urllib.parse import urlsplit

import jsonschema

ROOT=Path(__file__).resolve().parents[3]


def test_plugin_and_mcp_manifests_are_valid_portable_and_credentials_free():
    package=ROOT/'plugins/card-manager'
    for name in ('plugin','mcp'):
        schema=json.loads((ROOT/f'specs/028-chatgpt-card-manager/schemas/{name}.schema.json').read_text())
        document=json.loads((package/f'{name}.json').read_text())
        jsonschema.Draft202012Validator(schema).validate(document)
    mcp=json.loads((package/'mcp.json').read_text())
    for server in mcp['mcpServers'].values():
        assert server['type']=='streamable-http'
        url=urlsplit(server['url'])
        assert url.scheme=='https' and url.hostname=='cardsp.cn' and url.path=='/mcp'
        assert not url.username and not url.password and not url.query and not url.fragment
        assert not server.get('headers')
        assert not server.get('command')
    plugin=json.loads((package/'plugin.json').read_text())
    onboarding=plugin['extensions']['com.openai']['onboardingSkill']
    assert (package/onboarding).resolve().is_relative_to(package.resolve())
    assert (package/onboarding).is_file()
