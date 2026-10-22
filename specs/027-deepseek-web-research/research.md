# Research

## Decision

Use official raw JSON Search/Fetch and then a separate non-thinking deepseek-flash completion. This guarantees execution of the queries instead of relying on vendor-specific enable_search flags. Keep direct site retrieval and evidence validation.

## Evidence

Real requests from current production container on 2026-10-20: checklist/eBay/CardHobby Search all HTTP 200 with content. Fetch of DeepSeek official documentation and one CardHobby detail HTTP 200; two eBay details and TCDB home HTTP 404; Beckett detail HTTP 504. Captures are private runtime receipts; do not include API keys. Exact checklist query returned TCDB and Cardboard Connection content.

## Rationale

Existing three model-assisted search entry points already have deterministic gates and structured outputs. Explicit retrieval is a small change at these boundaries and works with the existing SDK. Failure on a protected webpage remains uncertainty, never fabricated sale evidence.

## Alternatives

Third-party skills/MCP add dependencies with no better guaranteed access to protected sale pages. Provider-native agent web tools require a separate conversation/tool protocol. Retaining Qwen fails the user's complete replacement objective; remove it from active configuration after validation.

Official reference: https://api-docs.deepseek.com/zh-cn/guides/search/ .
