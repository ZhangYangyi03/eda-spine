# phys -- the physical stage, and whether the proof survives it

The spine (`../spine.py`) stops at the gate netlist. This directory carries the
chain one stage further: synthesis mapped to a real library, then floorplan,
placement, clock-tree synthesis and detailed routing with OpenROAD, then the
same property asked again of the routed netlist.

## Why this exists

Everything upstream of this directory uses yosys's own internal cells. The claim
"the property survived synthesis" is weaker than it sounds while that is true:
internal cells are not a library, there is no buffer insertion, no clock tree, no
wire. The interesting question is whether the proof survives contact with the
things the physical stage actually does -- CTS inserting a `BUF_X4` in front of
the clock, filler cells, an optimiser that is allowed to restructure logic.

It does survive. That is a measurement, not an assumption:

```
phys/run_all.sh counter ../assertforge/bench/designs/counter.v props_counter.sv counter_check

Design area 36 u^2 61% utilization.
postroute    A_plain        rc=0  successful proof by k-induction
patched (dff_d): DFF_X1 _26_ (.D(_00_), -> DFF_X1 _26_ (.D(_02_)
NEG_dff_d    A_plain        rc=2  counterexample trace [basecase]
patched (mux_a): MUX2_X1 _16_ (.A(cnt[0]), -> MUX2_X1 _16_ (.A(cnt[1])
NEG_mux_a    A_plain        rc=0  successful proof by k-induction
```

Three lines, and the third one is the one worth reading twice.

## The three results, and what each one actually says

1. **postroute PASS.** The property "cnt advances by exactly one" holds of the
   placed-and-routed netlist: 18 logic cells, 13 fillers, three `BUF_X4` from
   clock-tree synthesis, routed inside a 25.57 x 25.57 um die. Synthesis and
   P&R did not break it.

2. **NEG_dff_d FAIL.** `DFF_X1 _26_`'s data input rewired from `_00_` to `_02_`
   -- i.e. the counter's bit 0 flop fed by bit 2's next-state term -- is caught,
   with a counterexample, adter 3 steps. So the gate can say no on this netlist,
   and the PASS above is not the gate failing to look.

3. **NEG_mux_a PASS, and that is the finding.** `MUX2_X1 _16_ .A(cnt[0])` changed
   to `.A(cnt[1])` and the property still holds. It has to: that mux's `A` input
   is the hold path, taken when `en = 0`, and the property is guarded by `pen`, so
   it never observes a hold. A change to the logic can be real and still be
   invisible to the property that was written.

   This is the same failure mode the RTL-level spine hit from the other side. Its
   first negative control was "drop `assume (rst)`", which came back PROVED
   because the assertion's own guard already covered the assumption. Both times
   the lesson is the same: **a control you did not check is not a control, and
   neither is a property you did not check the blindness of.**

## Files

    get_pdk.sh          fetch the nangate45 platform (persisted under $HOME)
    run_all.sh          the whole chain: synth -> P&R -> property -> controls
    run_physical.sh     yosys technology mapping + OpenROAD, one design
    physical.tcl        the OpenROAD script (floorplan .. detailed_route)
    check_property.sh   ask the property of any netlist, with the clock fallback
    mutate.sh           the two negative-control mutations
    constraint.sdc.tmpl the clock and IO timing, 2.0 ns

## What this cost, in the order it was paid

- **`openroad` is not on PATH on this host.** It is at
  `/opt/openroad/bin/openroad`, v2.0-17598, and it needs
  `LD_LIBRARY_PATH=/opt/python310/usr/lib/x86_64-linux-gnu` or it dies with
  `libpython3.10.so.1.0: cannot open shared object file`. Scanning PATH and
  concluding "openroad is missing" was wrong.
- **`[ERROR ORD-2010] no technology has been read`** fires on
  `initialize_floorplan`, several statements after the actual mistake. OpenROAD
  needs `read_lef` for the technology and the macro LEF *before* the liberty file
  means anything.
- **`read_liberty` needs a flag to be usable as a cell library for sby.** Plain
  `read_liberty -lib` gives blackboxes and sby says
  `Module \INV_X1 ... is a blackbox/whitebox module`. The functional form needs
  `-ignore_miss_func -ignore_miss_dir -ignore_miss_data_latch` -- because
  `NangateOpenCellLibrary_typical.lib` has clocks, and the clock-gating cells
  under a library that yosys 0.33 rejects outright with
  `Missing function on output IQ of cell CLKGATETST_X1`.
- **The clock is a derived clock after CTS**, and sby refuses that shape. The
  fallback is `clk2fflogic`; on this design the cheaper path is to let
  `flatten; opt -fast` remove the surviving buffer, which is what
  `check_property.sh` tries first.
- **`/tmp` inside WSL is not storage.** The PDK was fetched to `/tmp`, a
  `wsl --shutdown` was needed (the service wedged with `0x8007274c`), and the
  next sby run failed on a missing `.lib`. It lives under `$HOME/pdk` now.
- **A 6.7 MB download does not finish in 60 s here.** `curl -m 60` returns rc=28
  with a truncated file that yosys then rejects; `get_pdk.sh` retries with `-C -`.
