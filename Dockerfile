# VerityAI API image.
#
# z3-solver ships prebuilt wheels for common platforms, so no build
# toolchain is required for it specifically; gcc is kept as a fallback in
# case any other dependency lacks a wheel for the target platform.
FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --no-cache-dir .

ENV PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["uvicorn", "verityai.api.rest:app", "--host", "0.0.0.0", "--port", "8000"]
