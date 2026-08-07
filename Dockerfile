FROM python:3.12-slim
LABEL authors="qiqiandfei"

ARG TELEPIPLEX_COMMIT=unknown

ENV TELEPIPLEX_COMMIT="${TELEPIPLEX_COMMIT}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt /app/
RUN pip install --upgrade pip --no-cache-dir && \
    pip install -r requirements.txt --no-cache-dir

COPY ./app /app
COPY ./sdk /opt/telepiplex/sdk
COPY ./tools /opt/telepiplex/tools

RUN mkdir -p /config/plugins /tmp/telepiplex

ENV PYTHONPATH="/:/app:/app/utils:/opt/telepiplex/sdk/src"

VOLUME ["/config"]

CMD ["python", "115bot.py"]
