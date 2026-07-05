#!/bin/sh
set -eu

DOUBAN_API_DIR="${DOUBAN_API_DIR:-/mnt/user/appdata/douban-api}"
DOUBAN_API_REPO="${DOUBAN_API_REPO:-https://github.com/wanglin2/douban_api.git}"
DOUBAN_API_IMAGE="${DOUBAN_API_IMAGE:-douban-api:latest}"
DOUBAN_API_CONTAINER="${DOUBAN_API_CONTAINER:-douban-api}"
DOUBAN_API_PORT="${DOUBAN_API_PORT:-8085}"

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

mkdir -p "$DOUBAN_API_DIR"

if [ ! -d "$DOUBAN_API_DIR/.git" ]; then
    git clone "$DOUBAN_API_REPO" "$DOUBAN_API_DIR"
else
    git -C "$DOUBAN_API_DIR" pull --ff-only
fi

cp "$SCRIPT_DIR/Dockerfile" "$DOUBAN_API_DIR/Dockerfile"
cp "$DOUBAN_API_DIR/models/detail.js" "$DOUBAN_API_DIR/models/detail.legacy.js"
cp "$SCRIPT_DIR/patches/detail.js" "$DOUBAN_API_DIR/models/detail.js"

docker build -t "$DOUBAN_API_IMAGE" "$DOUBAN_API_DIR"
docker rm -f "$DOUBAN_API_CONTAINER" 2>/dev/null || true
docker run -d \
    --name "$DOUBAN_API_CONTAINER" \
    --restart unless-stopped \
    -p "$DOUBAN_API_PORT:8085" \
    -e OPENSSL_CONF=/tmp/openssl-empty.cnf \
    "$DOUBAN_API_IMAGE"

echo "Started $DOUBAN_API_CONTAINER on port $DOUBAN_API_PORT."
echo "Verify PhantomJS: docker exec $DOUBAN_API_CONTAINER sh -lc 'echo \$OPENSSL_CONF && phantomjs --version'"
echo "Verify API: curl -v \"http://<UNRAID_IP>:$DOUBAN_API_PORT/movie/detail?url=https%3A%2F%2Fmovie.douban.com%2Fsubject%2F4864908%2F\""
