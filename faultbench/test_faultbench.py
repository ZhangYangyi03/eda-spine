"""Tests for the netlist reader and the bit-parallel simulator.

The important test in this file is `test_off_set_cover_*` and its companions.
Everything else is here to keep the rest honest, but those exist because of a
bug that the test suite did not have and therefore did not catch.
"""

import itertools
import os
import random
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import blif                                            # noqa: E402
import run_faultbench as fb                            # noqa: E402

BENCH = os.path.join(HERE, "bench")


# --------------------------------------------------------------------------
# covers: on-set vs off-set. BLIF is not "unmatched rows are 0".
# --------------------------------------------------------------------------

def write(tmp_path, text, name="t.blif"):
    p = os.path.join(str(tmp_path), name)
    with open(p, "w") as f:
        f.write(text)
    return p


FIXTURES = os.path.join(HERE, "fixtures")


def test_the_recorded_minimal_cases_still_say_what_they_said():
    """fixtures/ holds the hand-written minimal cases the off-set rule was
    settled with, together with the answer each must produce. They are kept as
    files rather than as strings in this test so that the exact bytes that
    settled the question are still on disk, byte for byte, if the question ever
    comes up again."""
    expect = {
        "zero_row_01": "1011",       # 01 excluded -> everything else is 1
        "zero_row_11": "1110",
        "zero_rows_01_11": "1010",
        "zero_rows_10": "1101",
        "zero_dash": "1100",         # "1-" excluded -> a=1 is 0
        "one_row_01": "0100",        # a single 1-row: the on-set reading
        "xor": "0110",
        "or": "0111",
        "and": "0001",
        "parti": "0011",
        "empty": "0000",
        "const1": "1111",
        "invert": "1100",
    }
    for name, want in expect.items():
        net = blif.load(os.path.join(FIXTURES, name + ".blif"))
        got = "".join(str(int(net.simulate_reference({"a": bool(a), "b": bool(b)})["f"]))
                      for a in (0, 1) for b in (0, 1))
        assert got == want, (name, got, want)


def test_all_zero_cover_is_an_off_set_not_a_constant_zero(tmp_path):
    """A .names whose rows are all 0 lists the OFF-set: the output is 1 wherever
    the cover does not match. Reading it as a constant 0 is the bug this whole
    file is arranged around."""
    p = write(tmp_path, ".model t\n.inputs a b\n.outputs f\n.names a b f\n01 0\n.end\n")
    net = blif.load(p)
    got = "".join(str(int(net.simulate_reference({"a": bool(a), "b": bool(b)})["f"]))
                  for a in (0, 1) for b in (0, 1))
    # 01 is excluded, everything else is 1
    assert got == "1011", got


def test_all_zero_cover_with_several_rows(tmp_path):
    p = write(tmp_path, ".model t\n.inputs a b\n.outputs f\n.names a b f\n01 0\n11 0\n.end\n")
    net = blif.load(p)
    got = "".join(str(int(net.simulate_reference({"a": bool(a), "b": bool(b)})["f"]))
                  for a in (0, 1) for b in (0, 1))
    assert got == "1010", got


def test_one_row_is_an_on_set(tmp_path):
    """The other half of the rule: a single 1-row means unmatched gives 0. If
    both halves were read the same way, one of them would be wrong."""
    p = write(tmp_path, ".model t\n.inputs a b\n.outputs f\n.names a b f\n01 1\n.end\n")
    net = blif.load(p)
    got = "".join(str(int(net.simulate_reference({"a": bool(a), "b": bool(b)})["f"]))
                  for a in (0, 1) for b in (0, 1))
    assert got == "0100", got


def test_off_set_is_computed_after_the_rows_are_read(tmp_path):
    """The bug one level down: rows are appended AFTER the Node is constructed,
    so an `off_set` flag computed in __init__ reads an empty cover, says False,
    and turns every all-zero cover into a constant 0 -- the same wrong answer,
    now with a name that suggests it was checked."""
    p = write(tmp_path, ".model t\n.inputs a b\n.outputs f\n.names a b f\n01 0\n.end\n")
    net = blif.load(p)
    for n in net.nodes:
        if n.name == "f":
            assert n.cover, "the row should have been read"
            assert n.off_set is True


def test_both_simulators_know_the_off_set_rule(tmp_path):
    """`evaluate` and `simulate_reference` are separate implementations and both
    have to implement the same rule; agreeing on the wrong rule is the failure
    mode being guarded against here."""
    p = write(tmp_path, ".model t\n.inputs a b c\n.outputs f g\n"
                        ".names a b f\n01 0\n"
                        ".names b c g\n1- 1\n.end\n")
    net = blif.load(p)
    for a, b, c in itertools.product((0, 1), repeat=3):
        bits = {"a": bool(a), "b": bool(b), "c": bool(c)}
        slow = net.simulate_reference(bits)
        fast = net.evaluate({k: (1 << 0) * int(v) for k, v in bits.items()},
                            n_patterns=1)
        for o in net.outputs:
            assert bool(fast[o] & 1) == slow[o], (a, b, c, o)


# --------------------------------------------------------------------------
# the simulator, against exhaustive truth
# --------------------------------------------------------------------------

def random_netlist(n_inputs=6, n_nodes=24, seed=0):
    """A random combinational netlist, as BLIF text.

    Small enough to enumerate, big enough to have reconvergent fanout and covers
    of both kinds -- which is the point: a random circuit with a mix of on-set and
    off-set covers is the cheapest thing that can catch a misread cover.
    """
    rng = random.Random(seed)
    ins = ["i%d" % k for k in range(n_inputs)]
    wires = list(ins)
    lines = [".model t", ".inputs " + " ".join(ins)]
    outs = []
    for k in range(n_nodes):
        name = "n%d" % k
        fanin = rng.sample(wires, min(rng.choice((1, 2, 2, 3)), len(wires)))
        rows = []
        for bits in itertools.product("01", repeat=len(fanin)):
            if rng.random() < 0.4:
                rows.append("".join(bits) + " " + rng.choice("01"))
        if rows and not any(r[-1] == "1" for r in rows):
            pass                      # an off-set cover, deliberately kept
        if rng.random() < 0.15:
            rows = []                 # a constant 0 block
        lines.append(".names " + " ".join(fanin + [name]))
        lines.extend(rows)
        wires.append(name)
        if k >= n_nodes - 3:
            outs.append(name)
    lines.append(".outputs " + " ".join(outs))
    lines.append(".end")
    return "\n".join(lines) + "\n", outs


@pytest.mark.parametrize("seed", [0, 1, 2, 3, 4])
def test_fast_simulator_equals_the_reference_on_random_circuits(tmp_path, seed):
    text, outs = random_netlist(seed=seed)
    p = write(tmp_path, text)
    net = blif.load(p)
    full = 1 << net.n_inputs
    val = net.pattern_exhaustive()
    fast = net.evaluate(val, n_patterns=full)
    for v in range(full):
        bits = {n: bool((v >> i) & 1) for i, n in enumerate(net.inputs)}
        slow = net.simulate_reference(bits)
        for o in net.outputs:
            assert bool((fast[o] >> v) & 1) == slow[o], (seed, v, o)


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_the_exhaustive_pattern_set_catches_every_observable_fault(tmp_path, seed):
    """THE test. An exhaustive pattern set must have an escape rate of exactly 0:
    if a fault changes an output for some input vector, then the set containing
    every input vector catches it. Anything else means the simulator and the
    observability check disagree, and one of them is wrong.

    This is what found the off-set bug. It was 43.9% on `dec` and 95.5% on
    `int2float` and every unit test still passed, because the unit tests used
    hand-written covers that all had a 1-row in them.
    """
    text, _outs = random_netlist(seed=seed)
    p = write(tmp_path, text)
    net = blif.load(p)
    full = 1 << net.n_inputs
    val = net.pattern_exhaustive()
    ref = net.outputs_of(net.evaluate(val, n_patterns=full))

    # ground truth for "observable" by brute force, using the reference
    # simulator, independently of the bit-parallel one
    observable = []
    for name, kind in blif.all_faults(net):
        seen = False
        for v in range(full):
            bits = {n: bool((v >> i) & 1) for i, n in enumerate(net.inputs)}
            if any(net.simulate_reference(bits, fault=(name, kind))[o] !=
                   net.simulate_reference(bits)[o] for o in net.outputs):
                seen = True
                break
        if seen:
            observable.append((name, kind))
    assert observable, "a random circuit with no observable fault is not a test"

    caught = 0
    for name, kind in observable:
        out = net.outputs_of(net.evaluate(val, fault=(name, kind), n_patterns=full))
        if any(r != f for r, f in zip(ref, out)):
            caught += 1
    assert caught == len(observable), (
        "exhaustive set missed %d of %d observable faults -- the simulator and the "
        "observability check disagree" % (len(observable) - caught, len(observable)))


# --------------------------------------------------------------------------
# the real benchmarks
# --------------------------------------------------------------------------

needs_bench = pytest.mark.skipif(not os.path.isdir(BENCH),
                                 reason="run get_bench.sh for the EPFL benchmarks")


@needs_bench
def test_int2float_exponent_is_monotone_in_the_input():
    """A weak check, and it is here labelled as weak.

    int2float's E field must be nondecreasing in the input integer -- that is a
    property any int-to-float encoder has, whatever its field layout. It is NOT
    enough to catch the off-set bug: the wrong reading produced a monotone E too
    (measured), so this test would have passed while the campaign was wrong. The
    test that discriminates is `test_simulator_agrees_with_a_third_party_simulator`
    below; this one is kept because it is cheap and it is a property of the
    benchmark rather than of this code.
    """
    net = blif.load(os.path.join(BENCH, "int2float.blif"))
    full = 1 << net.n_inputs
    out = net.evaluate(net.pattern_exhaustive(), n_patterns=full)
    exp = [sum(int((out["E[%d]" % k] >> v) & 1) << k for k in range(3))
           for v in range(full)]
    for v in range(1, full):
        assert exp[v] >= exp[v - 1], (v, exp[v - 1], exp[v])


def _have(*tools):
    import shutil
    return all(shutil.which(t) for t in tools)


@needs_bench
@pytest.mark.skipif(not _have("yosys", "iverilog"),
                    reason="needs yosys and iverilog for the third-party comparison")
def test_simulator_agrees_with_a_third_party_simulator():
    """THE test that found the off-set bug, run as a test.

    The same benchmark is synthesised to Verilog by yosys and simulated by Icarus
    Verilog over every input vector; the primary outputs are compared against the
    bit-parallel simulator. Nothing here shares code with blif.py except the file
    it reads, which is the whole point: a second implementation inside the same
    module can share a misreading of the format, and this one cannot.

    It fails loudly today if the cover semantics regress, which is how it earned
    its place here rather than in a scratch script.
    """
    from crosscheck_iverilog import crosscheck
    for name in ("dec", "int2float"):
        r = crosscheck(os.path.join(BENCH, name + ".blif"))
        assert r["mismatches"] == 0, (name, r["mismatches"], r["examples"])


@needs_bench
def test_exhaustive_pattern_set_has_zero_escape_on_the_real_benchmarks():
    """The invariant, on the actual benchmark files: an exhaustive pattern set
    must catch every fault that changes an output. Measured 43.9% and 95.5%
    before the off-set fix and 0.0% after it, which is what makes this a test
    rather than a tautology."""
    import oracle
    for name in ("dec", "int2float"):
        net = blif.load(os.path.join(BENCH, name + ".blif"))
        verdicts = oracle.resolve_z3(net)
        observable = [f for f, v in verdicts.items() if v == "observable"]
        assert observable
        full = 1 << net.n_inputs
        val = net.pattern_exhaustive()
        ref = net.outputs_of(net.evaluate(val, n_patterns=full))
        missed = []
        for f in observable:
            out = net.outputs_of(net.evaluate(val, fault=f, n_patterns=full))
            if not any(r != g for r, g in zip(ref, out)):
                missed.append(f)
        assert not missed, (name, len(missed), missed[:5])


@needs_bench
def test_benchmarks_have_the_structure_this_module_assumes():
    """Combinational, topologically sorted, every reference defined. If a file
    ever stops satisfying this, the failure should be here and not inside a fault
    campaign three stages later."""
    for f in sorted(os.listdir(BENCH)):
        if not f.endswith(".blif"):
            continue
        net = blif.load(os.path.join(BENCH, f))
        assert not net.latches, f
        seen = set(net.inputs)
        for n in net.nodes:
            assert all(x in seen for x in n.fanin), (f, n.name, n.fanin)
            seen.add(n.name)
        assert net.inputs and net.outputs, f
