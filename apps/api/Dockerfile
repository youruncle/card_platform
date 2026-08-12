FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app
RUN python -m venv /app/.venv
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .

RUN useradd --create-home --uid 10001 cardplatform
RUN mkdir -p /app/uploads /app/scanner-jobs \
    && chown -R cardplatform:cardplatform /app/uploads /app/scanner-jobs
USER cardplatform

EXPOSE 8000
CMD ["uvicorn", "card_platform.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
