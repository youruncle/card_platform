"""Verify public routing and/or in-container settings; never prints credentials."""
import argparse
import json
import sys

import httpx


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url',help='Public origin, normally https://cardsp.cn')
    parser.add_argument('--runtime',action='store_true',help='Run inside API container/environment')
    args=parser.parse_args()
    if not args.base_url and not args.runtime:
        parser.error('provide --base-url or --runtime')
    results={}
    if args.runtime:
        from importlib.metadata import version
        from card_platform.config import get_settings
        from card_platform.mcp.deployment import check_runtime
        results.update(check_runtime(get_settings()))
        results['mcp_sdk_pinned']=version('mcp')=='1.30.0'
    if args.base_url:
        base=args.base_url.rstrip('/')
        try:
            with httpx.Client(timeout=20,follow_redirects=False) as client:
                response=client.post(base+'/mcp',headers={'Accept':'application/json, text/event-stream'},json={'jsonrpc':'2.0','id':1,'method':'tools/list','params':{}})
                results['mcp_requires_oauth']=response.status_code==401 and 'resource_metadata' in response.headers.get('www-authenticate','')
                resource=client.get(base+'/.well-known/oauth-protected-resource/mcp')
                results['resource_metadata']=resource.status_code==200 and resource.json().get('resource')==base+'/mcp'
                issuer=client.get(base+'/.well-known/oauth-authorization-server/v1/chatgpt/oauth')
                doc=issuer.json() if issuer.status_code==200 else {}
                results['oauth_metadata']=doc.get('issuer')==base+'/v1/chatgpt/oauth' and 'S256' in doc.get('code_challenge_methods_supported',[]) and doc.get('token_endpoint_auth_methods_supported')==['none']
                results['approval_page_available']=client.get(base+'/chatgpt/actions/00000000-0000-0000-0000-000000000000').status_code==200
        except (httpx.HTTPError,ValueError):
            results['public_verification_completed']=False
    print(json.dumps({'checks':results,'passed':all(results.values())},ensure_ascii=False,indent=2))
    return 0 if results and all(results.values()) else 1


if __name__=='__main__':
    sys.exit(main())
