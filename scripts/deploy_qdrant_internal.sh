#!/usr/bin/env bash
set -euo pipefail

# deploy_qdrant_internal.sh
# Usage: ./deploy_qdrant_internal.sh <tar-file> <internal-registry/namespace/repo:tag> [compose-file]
# Example: ./deploy_qdrant_internal.sh ./qdrant_qdrant_no-jemalloc.tar my-registry.local/qdrant/qdrant:v1.18.3 docker-compose.yml

if [ $# -lt 2 ]; then
  echo "Usage: $0 <tar-file> <internal-image> [compose-file]"
  exit 2
fi

TAR="$1"
TARGET_IMAGE="$2"
COMPOSE_FILE=${3:-"docker-compose.yml"}

echo "Loading ${TAR} into Docker on this host..."
docker load -i "${TAR}"

# Attempt to find a qdrant image name from loaded images
SRC_IMG=$(docker images --format '{{.Repository}}:{{.Tag}}' | grep -E '^qdrant/qdrant|^qdrant:' | head -n1 || true)
if [ -n "${SRC_IMG}" ]; then
  echo "Tagging ${SRC_IMG} -> ${TARGET_IMAGE}"
  docker tag "${SRC_IMG}" "${TARGET_IMAGE}"
else
  echo "Warning: could not auto-detect source qdrant image name. If tagging fails, please run 'docker images' to find the image and tag manually."
fi

echo "Pushing ${TARGET_IMAGE} to internal registry..."
docker push "${TARGET_IMAGE}"

# Optional: if docker-compose file exists, replace qdrant image and restart
if [ -f "${COMPOSE_FILE}" ]; then
  echo "Updating ${COMPOSE_FILE} to use ${TARGET_IMAGE} (creates a override file)"
  cat > docker-compose.qdrant.override.yml <<EOF
services:
  qdrant:
    image: ${TARGET_IMAGE}
EOF
  echo "Bringing up qdrant using docker compose (with override)"
  docker compose -f "${COMPOSE_FILE}" -f docker-compose.qdrant.override.yml up -d qdrant
  echo "Done."
else
  echo "Compose file ${COMPOSE_FILE} not found; skipping compose deploy. Image pushed to registry."
fi
