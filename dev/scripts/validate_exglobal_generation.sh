#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <top-level-config.yaml> [output-script]" >&2
  exit 2
fi

CFG="$1"
OUT="${2:-dev/scripts/exglobal_atmos_analysis.generated.sh}"

python3 dev/scripts/generate_exglobal_atmos_analysis.py \
  --config "$CFG" \
  --template dev/scripts/templates/exglobal_atmos_analysis.sh.j2 \
  --output "$OUT"

shellcheck "$OUT"
echo "Generated and shellcheck-clean: $OUT"
