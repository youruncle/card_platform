"""Provider-specific request parameters for explicitly non-thinking vision calls."""


def nonthinking_extra_body(model: str) -> dict:
    if model.startswith("deepseek-"):
        return {"thinking": {"type": "disabled"}}
    return {"enable_thinking": False}
