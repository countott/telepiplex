#!/usr/bin/env bash

set -euo pipefail

echo "Building telepiplex:latest..."
docker build -f Dockerfile -t telepiplex:latest .

echo "Build completed successfully."
docker image inspect telepiplex:latest --format 'Image: {{.RepoTags}} Size: {{.Size}} bytes'
