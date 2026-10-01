#!/bin/bash
# phys/check_property.sh <design> <netlist.v> <props.sv> <props_top> <label> [pdk]
#
# The question: does this netlist still satisfy the property that was proved of
# the RTL?
#
# Two shapes are tried, and the order matters:
#
#   A_plain        read the netlist, flatten, opt -fast. On this counter the
#                  clock buffer CTS inserted is optimised away by that pair, so
#                  `clk` is a real clock again and the proof runs in ~1 s.
#   B_clk2fflogic  the fallback for when it is not: if the netlist still presents
#                  a DERIVED clock, sby refuses outright --
#                    "derived signal clknet_1_0__leaf_clk driven by
#                     clkbuf_1_0__f_clk (BUF_X4) from module counter is used as
#                     clock, derived clocks are only supported with clk2fflogic"
#                  clk2fflogic turns the clocked design into a combinational one
#                  the same engine can still prove properties of. Measured on the
#                  same routed counter: 60 s without finishing, when the buffer
#                  survived into the SMT model. It is the fallback, not the path.
set -u
DESIGN="$1"; NET="$2"; PROPS="$3"; TOP="$4"; LABEL="$5"; PDK="${6:-$HOME/pdk/nangate45}"
W="${WORKDIR:-$HOME/edaspine_work}/$DESIGN"
# sby names its workdir after the .sby file, so a leftover directory from an
# earlier run makes it die with FileExistsError on A_plain. Start clean.
rm -rf "$W/check_$LABEL"; mkdir -p "$W/check_$LABEL"; cd "$W/check_$LABEL"
cp "$NET" netlist.v; cp "$PROPS" props.sv
LIB=$PDK/lib/NangateOpenCellLibrary_typical.lib

emit() {  # emit <file> <extra yosys lines>
  { echo "[options]"; echo "mode prove"; echo "depth 10"
    echo "[engines]"; echo "smtbmc z3"
    echo "[files]"; echo "netlist.v"; echo "props.sv"
    echo "[script]"
    echo "read_liberty -ignore_miss_func -ignore_miss_dir -ignore_miss_data_latch $LIB"
    echo "read -formal netlist.v"
    echo "read -formal -sv props.sv"
    echo "prep -top $TOP"
    echo "flatten"
    echo "opt -fast"
    [ -n "$2" ] && echo "$2"
  } > "$1"
}

emit A_plain.sby ""
timeout 300 sby -f A_plain.sby > A_plain.log 2>&1; RC=$?
if grep -q "^SBY .*ERROR" A_plain.log; then
  emit B_clk2fflogic.sby "clk2fflogic"
  timeout 300 sby -f B_clk2fflogic.sby > B_clk2fflogic.log 2>&1; RC=$?
  V=B_clk2fflogic
else
  V=A_plain
fi
printf "%-12s %-14s rc=%s  %s\n" "$LABEL" "$V" "$RC" \
  "$(grep -E 'successful proof|counterexample trace|^SBY .*ERROR' $V.log | head -2 | tr '\n' ' ')"
