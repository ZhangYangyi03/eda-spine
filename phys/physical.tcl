# The physical stage: from the equivalence-proven gate netlist to a placed,
# routed layout, using the same OpenROAD that ORFS drives.
#
# Why a hand-written TCL rather than ORFS's Makefile: ORFS's nangate45 flow wants
# a whole PDK tree (gds, lef, lib, cdl, tech) and a config per design, and the
# point here is not to reimplement ORFS. The point is that this chain has a
# physical stop at all, and that the stop is checkable.
#
# Cell naming note, measured: `abc -liberty` produces a netlist with `$_NOT_`
# style internal cells, not `INV_X1`. OpenROAD's `read_verilog` runs
# `techmap`-equivalent internally via `read_liberty`, so the file OpenROAD wants
# is the one yosys writes AFTER the technology map, and this is that file.
# OpenROAD needs its own mapping of the netlist in its own dialect:
#   read_verilog -> link_design -> ...
# OpenROAD needs the technology LEF before it can floorplan at all -- the error
# without it is `[ERROR ORD-2010] no technology has been read`, and it fires on
# initialize_floorplan, i.e. several statements after the missing file.
read_lef $::env(PDK_DIR)/lef/NangateOpenCellLibrary.tech.lef
read_lef $::env(PDK_DIR)/lef/NangateOpenCellLibrary.macro.mod.lef
read_liberty $::env(PDK_DIR)/lib/NangateOpenCellLibrary_typical.lib
read_verilog $::env(SYNTH_V)
link_design $::env(DESIGN)

initialize_floorplan -utilization 40 -aspect_ratio 1.0 \
  -core_space 2.0 -site FreePDK45_38x28_10R_NP_162NW_34O

make_tracks
set_wire_rc -layer metal3
place_pins -hor_layers metal5 -ver_layers metal6

read_sdc $::env(SDC)
global_placement
detailed_placement
improve_placement
clock_tree_synthesis -buf_list {BUF_X1 BUF_X2 BUF_X4} -root_buf BUF_X4 -sink_clustering_enable
detailed_placement
repair_timing -setup -skip_pin_swap
detailed_placement
global_route -guide_file route.guide
detailed_route -output_drc drc.rpt -output_maze maze.log -verbose 0
filler_placement "FILLCELL_X1 FILLCELL_X2 FILLCELL_X4 FILLCELL_X8"
check_placement -verbose
report_design_area
write_def $::env(OUT_DEF)
write_verilog $::env(OUT_V)
