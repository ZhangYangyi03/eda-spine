#!/bin/bash
# Fetch the nangate45 open PDK, the same platform OpenROAD's own flow (ORFS) uses.
#
# Two lessons are baked in here, both measured on this machine:
#   1. Into $HOME, not /tmp. This was first written to /tmp, and one
#      `wsl --shutdown` (needed because the WSL service wedged with 0x8007274c)
#      took the whole PDK with it; every sby run after that failed with
#      "Can't open input file .../NangateOpenCellLibrary_typical.lib".
#   2. Resumable. The typical.lib is 6.7 MB and a plain `curl -m 60` dies at
#      5.5 MB (rc=28), leaving a truncated file that yosys then rejects. -C -
#      plus a generous timeout, retried, is what actually completes here.
set -u
PDK="${1:-$HOME/pdk/nangate45}"
BASE=https://raw.githubusercontent.com/The-OpenROAD-Project/OpenROAD-flow-scripts/master/flow/platforms/nangate45
mkdir -p "$PDK"/lef "$PDK"/lib "$PDK"/cdl
want() {  # want <relpath> <minsize>
  local f="$1" min="$2"
  [ -s "$PDK/$f" ] && [ "$(stat -c%s "$PDK/$f")" -ge "$min" ] && return 0
  echo "fetching $f"
  for try in 1 2 3 4; do
    curl -sfL --retry 3 --retry-delay 2 -C - -m 300 -o "$PDK/$f" "$BASE/$f" && break
    sleep 2
  done
  local sz; sz=$(stat -c%s "$PDK/$f" 2>/dev/null || echo 0)
  [ "$sz" -ge "$min" ] || { echo "SHORT $f (got $sz, want $min)" >&2; return 1; }
}
want lef/NangateOpenCellLibrary.tech.lef        15000
want lef/NangateOpenCellLibrary.macro.mod.lef  200000
want lib/NangateOpenCellLibrary_typical.lib   6000000
want cdl/NangateOpenCellLibrary.cdl            200000
want cells_clkgate.v cells_latch.v cells_adders.v grid_strategy-M1-M4-M7.tcl tapcell.tcl 0 2>/dev/null || true
for f in cells_clkgate.v cells_latch.v cells_adders.v grid_strategy-M1-M4-M7.tcl tapcell.tcl; do
  [ -s "$PDK/$f" ] || curl -sfL -m 120 -o "$PDK/$f" "$BASE/$f"
done
echo "PDK ready at $PDK"
ls -la "$PDK" "$PDK/lef" "$PDK/lib" | grep -v '^total'
