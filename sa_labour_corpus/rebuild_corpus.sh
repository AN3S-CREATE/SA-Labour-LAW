#!/usr/bin/env bash
# Rebuild the corpus from scratch at a durable location.
# Every stage is re-runnable; this simply runs them in order.
#
# A failing stage stops the rebuild with a non-zero status. Every stage's
# output is filtered through grep and tail, so without pipefail a crashed
# stage would look like success and the later stages would index and report
# a half-built corpus under a "complete" banner. The greps are guarded with
# `|| true` because grep exits 1 when nothing matches, which is not a failure.
set -euo pipefail
trap 'echo "=== REBUILD FAILED (exit $?, line $LINENO) ===" >&2' ERR
OUT="${1:-./data/corpus}"
EMAIL="${2:-andriesl@salaria.co.za}"
KB="D:/D-Offload/01_Repositories_Programs_Software/Development/South African Law/CascadeProjects/windsurf-project/knowledge_base/documents.jsonl"

echo "=== 1. crawl all collectable sources ==="
python -m labour_corpus crawl --out "$OUT" --contact-email "$EMAIL" \
  --i-understand-public-sources 2>&1 | { grep -E ": stored=|summary" || true; } | tail -20

echo "=== 2. import the citation map ==="
python -m labour_corpus import-citation-map --from "$KB" --out "$OUT" 2>&1 | tail -3

echo "=== 3. import legacy documents ==="
python -m labour_corpus import-legacy-documents --from "$KB" --out "$OUT" 2>&1 | tail -3

echo "=== 4. parse with OCR ==="
python -m labour_corpus parse --out "$OUT" --ocr --ocr-dpi 220 2>&1 \
  | { grep -v -i "onnxruntime\|warning" || true; } | tail -4

echo "=== 5. index ==="
python -m labour_corpus index --out "$OUT" 2>&1 | tail -1

echo "=== 6. coverage report ==="
python -m labour_corpus report --out "$OUT" --contact-email "$EMAIL" 2>&1 | tail -1

echo "=== REBUILD COMPLETE ==="
