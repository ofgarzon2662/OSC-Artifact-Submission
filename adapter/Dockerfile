FROM python:3.11.16-alpine3.24@sha256:8e582494eafd8e4a58d5c85dd3e057e6914a14d4a67687908440775aa82c9b40 AS dependencies

WORKDIR /build

COPY requirements.runtime.lock .
RUN pip install --no-cache-dir --no-compile --require-hashes --only-binary=:all: \
    --target /opt/runtime-packages -r requirements.runtime.lock

FROM python:3.11.16-alpine3.24@sha256:8e582494eafd8e4a58d5c85dd3e057e6914a14d4a67687908440775aa82c9b40

WORKDIR /app

ENV PYTHONUNBUFFERED=1

RUN apk add --no-cache libuuid=2.42.3-r1 \
    && rm -rf /usr/local/lib/python3.11/site-packages/pip* \
      /usr/local/lib/python3.11/site-packages/setuptools* \
      /usr/local/lib/python3.11/site-packages/wheel* \
      /usr/local/lib/python3.11/site-packages/packaging* \
      /usr/local/bin/pip*

COPY --from=dependencies /opt/runtime-packages /usr/local/lib/python3.11/site-packages
COPY app.py ./

RUN addgroup -g 10001 -S osc \
    && adduser -u 10001 -S -D -H -G osc osc \
    && chown -R 10001:10001 /app

USER 10001:10001

EXPOSE 5000

CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "--threads", "4", "--timeout", "60", "app:app"]
