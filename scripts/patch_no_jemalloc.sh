#!/usr/bin/env bash
set -euo pipefail

# patch_no_jemalloc.sh
# Try to remove jemallocator dependency and replace global allocator usage with std::alloc::System
# Works best when run in the qdrant source root.

echo "Patching repository to prefer system allocator if jemallocator is present..."

if [ ! -f Cargo.toml ]; then
  echo "No Cargo.toml found in $(pwd); aborting patch script." >&2
  exit 1
fi

# Remove jemallocator dependency lines in Cargo.toml (best-effort)
if grep -q "jemallocator" Cargo.toml; then
  echo "Removing jemallocator dependency lines from Cargo.toml"
  sed -n '1,99999p' Cargo.toml > Cargo.toml.bak
  # delete lines containing jemallocator
  sed -i '/jemallocator/d' Cargo.toml || true
  # remove feature mentions such as "jemalloc" in features sections
  sed -i '/"jemalloc"/d' Cargo.toml || true
fi

# Find source files referencing jemallocator and patch them
FILES=$(grep -R --line-number "jemallocator" src 2>/dev/null | cut -d: -f1 | sort -u || true)
if [ -n "$FILES" ]; then
  for f in $FILES; do
    echo "Patching $f"
    cp "$f" "$f.bak" || true
    # replace use jemallocator::Jemalloc; with use std::alloc::System;
    sed -i 's/use[[:space:]]\+jemallocator::Jemalloc;/use std::alloc::System;/' "$f" || true
    # replace Jemalloc -> System
    sed -i 's/Jemalloc/System/g' "$f" || true
    # replace global allocator attribute patterns
    sed -i 's/\#\[global_allocator\][[:space:]]*static[[:space:]]\+ALLOC:.*;/# [global_allocator]\nstatic ALLOC: System = System;/' "$f" || true
  done
else
  echo "No jemallocator references found in src/ (ok)."
fi

echo "Patch complete (best-effort)."