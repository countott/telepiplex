FROM python:3.12-slim
LABEL authors="qiqiandfei"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt /app/
RUN pip install --upgrade pip --no-cache-dir && \
    pip install -r requirements.txt --no-cache-dir

ADD ./app .

ENV PYTHONPATH="/app:/app/utils:/app/core:/app/handlers:/app/.."

CMD ["python", "115bot.py"]
