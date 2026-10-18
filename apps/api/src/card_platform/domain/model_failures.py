"""Keep inference failure stages distinct from marketplace HTTP/empty results."""
import json

PREFIX = 'model_failure:'


def model_failure_reason(error, stage):
    diagnostic = str(error)
    code = ('model_access_denied' if 'AccessDenied.Unpurchased' in diagnostic
            or ('PermissionDenied' in type(error).__name__ and 'model' in diagnostic.casefold())
            else 'model_request_failed')
    return PREFIX + json.dumps(dict(stage=stage, code=code, error_class=type(error).__name__,
        diagnostic=diagnostic[:2000], diagnostic_truncated=len(diagnostic)>2000), ensure_ascii=False)


def parse_model_failures(messages):
    failures=[]
    for message in messages:
        text=str(message)
        if PREFIX in text:
            try:
                failures.append(json.loads(text.split(PREFIX,1)[1]))
            except (ValueError, TypeError):
                pass
        elif 'AccessDenied.Unpurchased' in text:
            failures.append(dict(stage='model_access',code='model_access_denied',diagnostic=text))
    return failures
