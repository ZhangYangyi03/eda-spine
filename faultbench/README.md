# faultbench — what a coverage number is actually measuring

A fault campaign on the [EPFL combinational benchmarks](https://github.com/lsils/benchmarks),
with the one thing a fault campaign usually does not have: an **oracle that is
not the detector**.

    get_bench.sh              fetch the benchmarks (9 files, 524 KB)
    blif.py                   read a .blif netlist, simulate it, break it
    oracle.py                 z3 (or full enumeration) decides what is detectable
    run_faultbench.py         the campaign, and the number that matters
    crosscheck_iverilog.py    yosys + Icarus Verilog as a third opinion
    metrics.py                detection / false-positive / escape, defined
    test_faultbench.py        19 tests, including the one that found the bug
    fixtures/                 the minimal cases the cover rule was settled with

## The claim under test

"The gate covered it, the campaign converged, coverage is high, the design is
checked." Every one of those sentences can be true while the design is broken.
This measures the gap on real circuits rather than arguing about it.

Two quantities, and they are not the same quantity:

    activation    the faulted circuit differs from the reference SOMEWHERE.
                  This is what a coverage database records: a wire toggled, a
                  branch was taken, a condition was hit.

    propagation   a PRIMARY OUTPUT differs. This is what "the fault was caught"
                  requires.

## Measured, on this host

    python run_faultbench.py --bench bench/dec.blif --bench bench/int2float.blif \
                             --bench bench/router.blif --patterns 128

    dec.blif        304 nodes    8 in 256 out |   928 faults (  28 distinct patterns)
        activation   674 (0.7263)  ->  propagation  674 (0.7263)
        oracle {'undetectable-by-construction': 16, 'observable': 912}
        of the 912 faults a test COULD catch, this set caught 674 -- escape 26.1%
    int2float.blif  260 nodes   11 in   7 out |   802 faults (  51 distinct patterns)
        activation   742 (0.9252)  ->  propagation  567 (0.7070)   [23.6% of what it called caught was not]
        oracle {'undetectable-by-construction': 580, 'observable': 222}
        of the 222 faults a test COULD catch, this set caught 168 -- escape 24.3%
    router.blif     284 nodes   60 in  30 out |   972 faults ( 128 distinct patterns)
        activation   714 (0.7346)  ->  propagation   82 (0.0844)   [88.5% of what it called caught was not]
        oracle {'undetectable-by-construction': 151, 'observable': 821}
        of the 821 faults a test COULD catch, this set caught 81 -- escape 90.1%

Read the third column and then the second. On `router`, a detector that watches
for activation reports 0.73 as its detection rate while 88.5% of the faults it
claims to have caught changed no output at all. On `int2float` the same detector
reports 0.93 as coverage, and of the 222 faults that can actually be observed it
found 169 — it is not that the number is pessimistic, it is that the number is
measuring a different event.

And the 60%-of-an-input-space question matters more than the rate:

    dec       128 patterns cover  28 distinct input vectors -- 10.94% of the space
    int2float 2048 patterns cover  55 distinct input vectors --  2.69% of the space

A random pattern set with a per-pattern sparsity (quarter of the inputs high) is
not sampling an input space, it is resampling a small corner of it. That is why
the escape rate looks flat and then falls off a cliff, and it is why a campaign
that stops when its number stops moving has stopped for a reason that has nothing
to do with the design.

The endpoint is the part that can be quoted without an asterisk:

    dec.blif       256 set /   256 distinct (100.00%) EXH  escape 0.0000
    int2float.blif 2048 set /  2048 distinct (100.00%) EXH  escape 0.0000

## Two oracles, and the faults that must leave the denominator

`oracle.py` decides observability with z3 over the whole circuit, or by
enumerating the input space on small ones. On `int2float` the two agree on all
802 faults. That number is not decoration: **580 of those 802 faults are stuck-at
faults no output can ever depend on.** A detection rate computed over the raw
fault list is not a statement about the detector, it is a statement about the
fault list. Reporting one without removing those is arithmetic, not measurement.

## The bug this directory found

`blif.py` read every `.names` cover as an on-set. BLIF says the cover is **either**
an on-set or an off-set, decided by the output column — if every row's output is
0, the listed patterns are the *off*-set and an unmatched input gives 1.

That is not a corner case on these benchmarks: int2float has 260 all-zero covers
out of 260 blocks, adder 1020 of 1020, priority 978 of 978. Reading them as
constants turned a whole class of logic into nothing.

How it was caught, and how it was not:

- **not** by reading the code, and **not** by the unit tests — they all passed,
  because every one of them used a hand-written cover with a 1-row in it;
- by the invariant that an EXHAUSTIVE pattern set must have an escape rate of
  exactly 0. It was 43.9% on `dec`, which cannot be true if the simulator is
  right, so the simulator was wrong;
- and then settled with `crosscheck_iverilog.py`: yosys writes the benchmark to
  Verilog, Icarus Verilog simulates every input vector, and the primary outputs
  are compared with the bit-parallel simulator. `int2float` disagreed on 9162 of
  14336 output bits. `dec` agreed, which is why the bug survived a benchmark
  that looked like it worked — `dec`'s 0-rows happened to be unreachable.

That comparison is now `test_simulator_agrees_with_a_third_party_simulator` and
runs in CI. The invariant is now
`test_exhaustive_pattern_set_has_zero_escape_on_the_real_benchmarks`.

Bugs like this are the reason the oracle is a separate module and not a function
inside the campaign. A campaign that decides for itself which faults count cannot
notice that it is misreading its input.

## What this does not do

- **Combinational only.** A `.latch` makes `load` refuse the file rather than
  quietly model a sequential circuit as a combinational one.
- **Random patterns, not ATPG.** This measures what a coverage-driven flow gets,
  which is the question. A deterministic ATPG tool would do better, and the point
  of the numbers is not that random is bad, it is that activation is not
  propagation.
- **The z3 pass is the slow part** (29 s for 802 faults on int2float) and it is
  cached on disk by benchmark, because its answer does not depend on the pattern
  set. `--oracle-budget` bounds it and unreached faults come back as `unknown`
  rather than being guessed into either side.
- **Not integrated with `../rtl` or `../phys` yet.** This is the third rung of
  the same ladder those two are on, and the honest next step is to feed a real
  post-synthesis netlist through it rather than the benchmark files.

## Reproducing

    bash get_bench.sh                      # EPFL benchmarks
    python3 -m pytest -q test_faultbench.py
    python3 run_faultbench.py --all --patterns 128 --saturation --json r.json

Needs `numpy` for nothing, `z3-solver` for the oracle, and `yosys`+`iverilog` for
the cross-check. On this host they are installed in WSL; the toolchain does not
exist on the Windows side.
