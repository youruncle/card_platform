"""Opt-in audit tracing; no sink means no logging, persistence or billing assumptions."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
import json
from time import perf_counter
from uuid import uuid4


@dataclass(frozen=True)
class PriceRate:
    currency: str
    version: str
    input_per_million: Decimal
    cached_per_million: Decimal
    output_per_million: Decimal


class AuditBudgetExceeded(RuntimeError):
    pass


@dataclass
class TraceRun:
    sink: object
    card_id: str | None = None
    prices: dict = field(default_factory=dict)
    max_model_calls: int | None = None
    model_calls: int = 0
    run_id: str = field(default_factory=lambda: str(uuid4()))


_CURRENT = ContextVar('valuation_audit_trace', default=None)


@contextmanager
def trace_run(sink, *, prices=None, card_id=None, max_model_calls=None):
    if max_model_calls is not None and max_model_calls < 0:
        raise ValueError('model budget must be nonnegative')
    run = TraceRun(sink, card_id, prices or {}, max_model_calls)
    token = _CURRENT.set(run)
    try:
        yield run
    finally:
        _CURRENT.reset(token)


def content_hash(value):
    data = value if isinstance(value, bytes) else json.dumps(
        value, sort_keys=True, ensure_ascii=False, default=str).encode()
    return sha256(data).hexdigest()


def emit(stage, status, **fields):
    run = _CURRENT.get()
    if run is not None:
        run.sink({'schema_version': 1, 'run_id': run.run_id, 'card_id': run.card_id,
                  'stage': stage, 'status': status, **fields})


def _field(value, key, default=None):
    return value.get(key, default) if isinstance(value, dict) else getattr(value, key, default)


async def traced_completion(create, *, stage, **kwargs):
    run = _CURRENT.get()
    if run is None:
        return await create(**kwargs)
    if run.max_model_calls is not None and run.model_calls >= run.max_model_calls:
        emit(stage, 'blocked', reason_codes=['model_call_budget_exhausted'])
        raise AuditBudgetExceeded('model call budget exhausted')
    run.model_calls += 1
    started = perf_counter()
    event = dict(call_id=str(uuid4()), model=kwargs.get('model'),
                 started_at=datetime.now(UTC).isoformat(), input_hash=content_hash(kwargs),
                 cost_amount=None, cost_currency=None, price_version=None,
                 input_tokens=None, cached_tokens=None, output_tokens=None,
                 usage_missing_reason='provider_usage_unavailable',
                 retry_count=None, retry_scope='SDK internal retries not separately exposed')
    try:
        result = await create(**kwargs)
        usage = _field(result, 'usage')
        if usage is not None:
            input_tokens = _field(usage, 'prompt_tokens')
            output_tokens = _field(usage, 'completion_tokens')
            cached = _field(_field(usage, 'prompt_tokens_details'), 'cached_tokens', 0)
            for key, value in (('input_tokens',input_tokens),('output_tokens',output_tokens),('cached_tokens',cached)):
                if isinstance(value,int) and not isinstance(value,bool) and value >= 0:
                    event[key] = value
            if all(isinstance(t, int) and not isinstance(t, bool) and t >= 0
                   for t in (input_tokens, output_tokens, cached)) and cached <= input_tokens:
                event.update(input_tokens=input_tokens, output_tokens=output_tokens,
                             cached_tokens=cached, usage_missing_reason=None)
                rate = run.prices.get(kwargs.get('model'))
                if rate:
                    amount = ((input_tokens-cached)*rate.input_per_million
                              + cached*rate.cached_per_million
                              + output_tokens*rate.output_per_million)/Decimal(1000000)
                    event.update(cost_amount=str(amount), cost_currency=rate.currency,
                                 price_version=rate.version)
                else:
                    event['cost_missing_reason'] = 'versioned_price_not_configured'
            else:
                event['usage_missing_reason'] = ('provider_cache_usage_unavailable' if cached is None
                    and event['input_tokens'] is not None and event['output_tokens'] is not None
                    else 'provider_usage_invalid_or_incomplete')
        emit(stage, 'succeeded', elapsed_ms=(perf_counter()-started)*1000, **event)
        return result
    except BaseException as error:
        emit(stage, 'failed', elapsed_ms=(perf_counter()-started)*1000,
             error_type=type(error).__name__, **event)
        raise
