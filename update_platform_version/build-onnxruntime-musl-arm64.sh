#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
OUTPUT_DIR="$SCRIPT_DIR/onnxruntime-musl"

rm -rf "$OUTPUT_DIR"
mkdir -p "$OUTPUT_DIR"

docker buildx build \
  --platform linux/arm64 \
  --progress=plain \
  --file "$PROJECT_DIR/WHartTest_Actuator/Dockerfile.onnxruntime-musl-builder" \
  --target artifact \
  --output "type=local,dest=$OUTPUT_DIR" \
  "$PROJECT_DIR/WHartTest_Actuator"

test -f "$OUTPUT_DIR/verified"
sha256sum "$OUTPUT_DIR"/*.whl > "$OUTPUT_DIR/SHA256SUMS"

printf '%s\n' "ONNX Runtime ARM64/musl wheel generated and verified:"
cat "$OUTPUT_DIR/SHA256SUMS"
