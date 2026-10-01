# eda-spine

The chain between the four tools. `autoforge` orchestrates; these are the three
domain stops it drives:

    qoragent      search synthesis passes, keep only equivalence-proven wins
    assertforge   properties, proved and refuted by sby/yosys/z3
    covagent      per-bit toggle coverage, read from the RAW database

Until this repo existed, no file in any of the three imported another one of
them -- the only cross-reference between them was one sentence in covagent's
README. This is that file. It reimplements none of them; it calls them in order
and hands each stage the artefact the previous stage produced.

    python spine.py --rtl <design.v> --top <top> --props <props.sv> --design <case>
    python spine.py ... --json out.json          # the full record
    python spine.py ... --no-coverage            # skip the simulator stage

## What the chain adds that no single tool has

qoragent's gate is *equivalence*: the netlist computes what the RTL computes.
That is one property, and a property set is not the design. So the question that
only exists once the tools are joined:

    does the POST-SYNTHESIS gate netlist still satisfy the property that
    assertforge proved of the RTL?

and the companion question on the same file:

    does every output bit still move under stimulus, on the netlist?

Both are asked here, and both are answered by tools that share exactly one
artefact between them -- `netlist.v`.

## The result that matters: an ordering no single tool can produce

    QoR wins vs the RTL        (qoragent)
    the netlist proves the property        (assertforge)  <- the seam holds
    one line perturbed refutes it          (assertforge)  <- the gate can say no

Three cases, every number measured on this host, all with verdict SPINE_OK:

    case          cells kept   formal: RTL / netlist / mutant / FALSE-control
    counter       10 -> 8      PROVED / PROVED / REFUTED / REFUTED
    traffic_fsm   15 -> 4      PROVED / PROVED / REFUTED / REFUTED
    sync_fifo    137 -> 137    no win kept; the netlist proof TIMEOUTs (see limits)

## Why one stage is not enough: coverage and formal disagree, measurably

The same mutants are run through both stages (`counter` and `traffic_fsm`):

    mutant                       formal      coverage
    counter   stuck_bit0         REFUTED     DEAD: cnt[3:0] never toggles
    counter   swap_en_gating     REFUTED     invisible: {0:17, 1:8, 2:4, 3:2}
    fsm       state_seq          REFUTED     DEAD: state[1] never toggles
    fsm       tick_gate          REFUTED     invisible: {0:6, 1:16}, all bits live

Read the two right-hand columns as a pair. `stuck_bit0` and `state_seq` are what
coverage is for: a bit that stops moving is the cheapest possible signal, and no
simulator argument is needed to believe it. `swap_en_gating` and `tick_gate` are
what coverage cannot do: both keep every bit toggling, both pass a coverage
closure, and both are functionally wrong -- only the property sees them. So the
formal stage catches everything here and the coverage stage nevertheless earns
its place: it is the one that needs no property to have been written correctly,
and it is the only stage that is a pure measurement rather than an argument.

## The control that failed first, and what it cost to repair

The natural negative control -- take the property, drop `assume (rst)` -- was
measured and came back **PROVED on a clean netlist**. The assertion's own
`!prst` guard already covered what the assumption covered, so the "control" was
a property that could not fail. A gate with a control that cannot fail has no
control. The shipped control strips the guards too (`if (started)` instead of
`if (started && pen && !prst)`), which is plainly false of a correct counter,
and the run is only SPINE_OK if the gate REFUTES it.

This is the same failure mode the whole repo is about, one level up: a check
that passes whatever it is given is decoration, and the way to find out is to
give it something it must reject. It now does that on every run.

## The surfaces, and why the last one is the interesting one

    tool          speaks              artefact out
    qoragent      RTL + yosys script  netlist.v            (Verilog)
    assertforge   RTL + assumptions    PROVED / REFUTED     (one sby job per question)
    covagent      a raw coverage db    cov.dat, per bit

The coverage surface is not the lcov summary, and that is deliberate.
`verilator_coverage --write-info` keeps line records only -- measured on
Verilator 5.020: 27 line records, 0 toggle records -- so the summary cannot
answer a per-bit question at all. This chain reads the raw database with
covagent's own parser (`boundary.parse_dump` / `toggles` / `bits_of`), which is
also why covagent's own seam work is per-bit.

One more measurement worth keeping: Verilator's generated `main` never calls
`coveragep()->write()`, so `--coverage` on its own builds an instrumented binary
and throws the data away (`cov.dat` is not created). The `sim_main.cpp` used
here is covagent's own replacement main, copied from its bench.

## The physical stage

That limit is closed. `phys/` runs the chain to a placed and routed layout on the
open nangate45 library and asks the same property of the result. The short
version, and the third line is the finding:

    postroute    A_plain   rc=0  successful proof by k-induction
    NEG_dff_d    A_plain   rc=2  counterexample trace [basecase]
    NEG_mux_a    A_plain   rc=0  successful proof by k-induction

Synthesis and P&R do not break the property (18 logic cells, 13 fillers, three
`BUF_X4` from clock-tree synthesis, routed in a 25.57x25.57 um die). Breaking a
flop's data input does get caught. Breaking a hold-path mux input does not -- and
cannot, because the property is guarded by `pen` and never observes a hold. A
real change can be invisible to the property that was written; `phys/README.md`
has the details and the five toolchain traps paid to get there.

## Honest limits

- **OpenROAD is not on PATH.** It is at `/opt/openroad/bin/openroad` and needs
  `LD_LIBRARY_PATH=/opt/python310/usr/lib/x86_64-linux-gnu`; scanning PATH and
  concluding it was absent was wrong. `phys/` uses it.
- **The netlist-instead-of-RTL runtime is the real cost.** `sync_fifo` proves in
  ~1 s against the RTL and TIMEOUTs against the flat 137-cell netlist. The seam
  check is affordable on small designs and is not free on larger ones; that is
  a property of SAT over a flattened netlist, not a bug to fix.
- **Coverage is measured with one stimulus per design**, hand-written and living
  in this repo. It answers "do these bits move under this stimulus", not "is
  every bit reachable".
- **The mutation table is per-design and by hand.** If a netlist's shape changes
  and no pattern matches, the run stops with an error rather than inventing a
  verdict -- that is the intended behaviour, and the fix is to edit `MUTANTS`,
  never to widen the match.
- **Three designs.** counter, traffic_fsm, sync_fifo. ISCAS/EPFL benchmarks are
  the obvious next thing and are not here yet.

## Layout

    spine.py               the chain: search -> prove -> perturb -> cover
    covcheck.py            the coverage stage (builds, runs, reads the raw db)
    props_counter.sv       per-case properties, each proved against the RTL first
    props_traffic_fsm.sv
    props_sync_fifo.sv
    spine_*.json           the recorded result of the last run of each case

Requires WSL with `yosys`, `sby`, `z3` and `verilator`, plus the three tool
repos checked out as siblings (`../qoragent`, `../assertforge`, `../covagent`).
