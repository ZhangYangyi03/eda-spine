#!/bin/bash
# Fetch the EPFL combinational benchmarks (lsils/benchmarks), the standard set
# for logic-synthesis work. .blif because that is how they ship and yosys reads
# it directly.
#
# Kept out of git on purpose beyond a small subset: multiplier.blif alone is
# 853 KB and arbiter.blif 361 KB, and a repo should not carry a megabyte of
# someone else's data when four lines fetch it. --all gets everything.
set -eu
DEST="$(cd "$(dirname "$0")" && pwd)/bench"
BASE=https://raw.githubusercontent.com/lsils/benchmarks/master
ARITH="adder bar div multiplier sin sqrt square log2 max hypot"
CTRL="ctrl router arbiter i2c int2float priority voter dec"
SUBSET="adder bar ctrl router int2float priority i2c voter dec"

want() {
  local group="$1" name="$2"
  local f="$DEST/$name.blif"
  [ -s "$f" ] && return 0
  if curl -sfL -m 60 -o "$f" "$BASE/$group/$name.blif"; then
    printf "  %-14s %8s bytes\n" "$name.blif" "$(stat -c%s "$f")"
  else
    rm -f "$f"; echo "  $name: not in $group" >&2; return 1
  fi
}

mkdir -p "$DEST"
if [ "${1:-}" = "--all" ]; then
  for n in $ARITH; do want arithmetic "$n" || true; done
  for n in $CTRL;  do want random_control "$n" || true; done
else
  for n in $SUBSET; do
    want arithmetic "$n" 2>/dev/null || want random_control "$n" 2>/dev/null || true
  done
fi
echo "bench: $(ls "$DEST" | wc -l) files, $(du -sh "$DEST" | cut -f1)"
