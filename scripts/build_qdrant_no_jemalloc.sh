#!/usr/bin/env bash
set -euo pipefail

# build_qdrant_no_jemalloc.sh
# Usage: ./build_qdrant_no_jemalloc.sh [image:tag] [qdrant-tag-or-branch] [out-tar]
# Example: ./build_qdrant_no_jemalloc.sh my-qdrant:no-jemalloc v1.18.3 ./qdrant_v1.18.3_nojemalloc.tar

IMAGE_TAG=${1:-"qdrant:no-jemalloc"}
QDRANT_REF=${2:-"main"}
OUT_TAR=${3:-"./qdrant_${IMAGE_TAG//[:/]/_}.tar"}

echo "Building image ${IMAGE_TAG} from qdrant ref ${QDRANT_REF} (platform linux/arm64)"

# ensure buildx available
docker buildx create --use --driver docker-container >/dev/null 2>&1 || true

docker buildx build \
  --platform linux/arm64 \
  --build-arg QDRANT_VERSION=${QDRANT_REF} \
  -t ${IMAGE_TAG} \
  -f docker/Dockerfile.nojemalloc \
  . --load

if [ -f "${OUT_TAR}" ]; then
  echo "Overwriting existing ${OUT_TAR}"
  rm -f "${OUT_TAR}"
fi

echo "Saving image ${IMAGE_TAG} -> ${OUT_TAR}"
docker save -o "${OUT_TAR}" "${IMAGE_TAG}"

echo "Done. Tar saved at ${OUT_TAR}."
