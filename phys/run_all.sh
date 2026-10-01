#!/bin/bash
# The whole chain, RTL to a routed layout and back to a proved property.
#
#   phys/run_all.sh <design> <rtl.v> <props.sv> <props_top>
#
# Steps, and why each one is here:
#   0  PDK            nangate45, persisted under $HOME (see get_pdk.sh)
#   1  synthesis      yosys -> dfflibmap/abc -> a technology-mapped netlist
#   2  physical       OpenROAD: floorplan, place, CTS, route -> routed netlist
#   3  property       the property proved of the RTL, asked of the routed netlist
#   4  negative       the same question of a deliberately broken routed netlist
#
# Step 4 is not optional. A PASS in step 3 means nothing until step 4 shows the
# same gate can say no; the first attempt at step 4 used a rewiring that the
# property was blind to and came back PASS, which is how that rule got written.
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"
DESIGN="${1:?design}"; RTL="${2:?rtl}"; PROPS="${3:?props}"; TOP="${4:?props top}"
PDK="${PDK_DIR:-$HOME/pdk/nangate45}"
W="${WORKDIR:-$HOME/edaspine_work}/$DESIGN"

[ -s "$PDK/lib/NangateOpenCellLibrary_typical.lib" ] || bash "$HERE/get_pdk.sh" "$PDK"
bash "$HERE/run_physical.sh" "$DESIGN" "$RTL" "$PDK"
echo
bash "$HERE/check_property.sh" "$DESIGN" "$W/${DESIGN}_routed.v" "$HERE/../$PROPS" "$TOP" postroute

echo
NEG="$W/check_negpost/netlist.v"; mkdir -p "$W/check_negpost"
bash "$HERE/mutate.sh" "$W/${DESIGN}_routed.v" "$NEG" dff_d
bash "$HERE/check_property.sh" "$DESIGN" "$NEG" "$HERE/../$PROPS" "$TOP" NEG_dff_d
echo
NEG2="$W/check_negpost_mux/netlist.v"; mkdir -p "$W/check_negpost_mux"
bash "$HERE/mutate.sh" "$W/${DESIGN}_routed.v" "$NEG2" mux_a
bash "$HERE/check_property.sh" "$DESIGN" "$NEG2" "$HERE/../$PROPS" "$TOP" NEG_mux_a
