#!/bin/bash
# phys/run_physical.sh <design> <rtl.v> [pdk_dir]
set -e
DESIGN="$1"; RTL="$2"; PDK="${3:-$HOME/pdk/nangate45}"
W="${WORKDIR:-$HOME/edaspine_work}/$DESIGN"
HERE="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$W"; cp "$RTL" "$W/$DESIGN.v"
cd "$W"
LIB=$PDK/lib/NangateOpenCellLibrary_typical.lib
# --- synthesis, technology-mapped: what OpenROAD can link ---
yosys -q -p "
read_verilog $DESIGN.v
hierarchy -top $DESIGN
proc; opt; fsm; opt; memory; opt; techmap; opt
dfflibmap -liberty $LIB
abc -liberty $LIB
clean
write_verilog -noattr ${DESIGN}_synth.v
" 2>&1 | grep -v "^Warning" || true
sed "s/^current_design .*/current_design $DESIGN/" "$HERE/constraint.sdc.tmpl" > $DESIGN.sdc
export PDK_DIR=$PDK SYNTH_V=${DESIGN}_synth.v DESIGN=$DESIGN SDC=$DESIGN.sdc \
       OUT_DEF=$DESIGN.def OUT_V=${DESIGN}_routed.v
export LD_LIBRARY_PATH=/opt/python310/usr/lib/x86_64-linux-gnu:$LD_LIBRARY_PATH
/opt/openroad/bin/openroad -no_init -exit "$HERE/physical.tcl" > openroad.log 2>&1 || {
  echo "OPENROAD FAILED"; tail -20 openroad.log; exit 1; }
grep -E "Design area|ERROR" openroad.log | tail -3
echo "workdir: $W"; ls -la "$W" | grep -E "synth|routed|\.def"
