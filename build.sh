#!/usr/bin/env bash

set -euo pipefail

echo "Building telepiplex-core:latest..."
docker build -f Dockerfile -t telepiplex-core:latest .

echo "Build completed successfully."
docker image inspect telepiplex-core:latest --format 'Image: {{.RepoTags}} Size: {{.Size}} bytes'
