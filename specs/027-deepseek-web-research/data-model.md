# Data model

ResearchCapture: provider=deepseek, captured_at (UTC), actual queries, pages, classified errors. Page: query, public source URL, title, summary, bounded Search content and Fetch content, SHA-256, truncation marker, fetch_status. HTTP 200 empty Search is a normal empty observation. A failed Fetch does not erase successful Search content or raise its evidence grade.

Capture membership uses validated canonical source URLs with fragments removed. Only explicit HTTP(S) public hostnames without credentials are accepted, and callers can constrain site domains. Model-generated URLs absent from the capture are discarded.

Store capture in existing recognition/result JSON, not a new table. Existing market evidence, identity, sale independence, visual verification and transaction state remain unchanged.
