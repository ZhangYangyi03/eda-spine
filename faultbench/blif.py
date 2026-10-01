"""Read a .blif netlist, simulate it, and break it on purpose.

Why this file exists rather than shelling out to yosys for each step: the
experiment in `run_faultbench.py` needs to simulate one circuit hundreds of times
with one connection altered each time. Driving a synthesis tool per mutant would
cost seconds each; here a whole netlist is a list of small truth tables and a
pattern set is a Python int, so a simulation is a few hundred bitwise AND/OR/XOR
on 1024-bit integers and a mutant costs microseconds-to-milliseconds.

The simulator is bit-parallel: bit `p` of every wire's integer is that wire's
value under input pattern `p`. That is the same trick a coverage database uses
when it stores a toggle per bit rather than a waveform.

Everything here is combinational. A `.latch` makes `load` refuse the file rather
than silently treat the circuit as combinational -- the benchmarks in bench/ have
none, and pretending otherwise is how a fault experiment produces a wrong number.

THE ONE THING THAT WAS WRONG HERE

A `.names` block is a cover, and BLIF says the cover is EITHER an on-set or an
off-set, decided by the output column: if any row's output is 1 the rows are the
on-set, and an input combination no row matches gives 0; if every row's output is
0 the rows are the off-set, and an unmatched combination gives 1.

This module read every cover as an on-set. On the EPFL benchmarks that is a real
bug and not a corner case: int2float has 260 all-zero covers out of 260 blocks,
adder 1020 out of 1020, priority 978 of 978. It was caught by cross-checking
against yosys+iverilog (`crosscheck_iverilog.py`) on a benchmark whose outputs
have an external meaning -- int2float must produce a monotonically increasing
exponent -- and NOT by reading the code, and not by the unit tests, which all
passed because they used hand-written covers with a 1-row in them.
"""

import collections
import os
import random


class BlifError(Exception):
    pass


def _expand_continuation_lines(text):
    """BLIF continues a line with a trailing backslash. Nothing else does."""
    out = []
    buf = ""
    for raw in text.splitlines():
        line = raw.rstrip()
        if line.endswith("\\"):
            buf += line[:-1] + " "
            continue
        out.append(buf + line)
        buf = ""
    if buf:
        out.append(buf)
    return out


class Node:
    """One `.names` block: a small truth table with one output.

    `cover` is a list of (input_pattern, output_value) rows, where input_pattern
    is a string over {0,1,-} of the same length as `fanin`. A box with no rows is
    constant 0 (BLIF's own convention), which is why an empty .names block shows
    up as `const0` and not as an error.
    """

    __slots__ = ("name", "fanin", "cover", "is_const")

    def __init__(self, name, fanin, cover, is_const=None):
        self.name = name
        self.fanin = tuple(fanin)
        self.cover = cover
        self.is_const = is_const
    @property
    def off_set(self):
        """True when every row's output column is 0: BLIF then means the listed
        patterns are the OFF-set. Computed, not stored: rows are appended to
        `cover` after the Node is built, so a flag set in __init__ would always
        read an empty cover and always say False -- which is exactly the bug this
        property fixes, one level down from the bug it fixes."""
        return bool(self.cover) and not any(res for _pat, res in self.cover)


class Netlist:
    def __init__(self, model, inputs, outputs, nodes, latches):
        self.model = model
        self.inputs = inputs            # ordered, as written
        self.outputs = outputs
        self.nodes = nodes              # ordered as written: BLIF is topologically sorted
        self.latches = latches

    # -- construction ------------------------------------------------------
    @property
    def n_inputs(self):
        return len(self.inputs)

    def load(self):
        """Check the file is what this class claims it can handle."""
        if self.latches:
            raise BlifError("design has %d .latch -- this module is combinational only"
                            % len(self.latches))
        for n in self.nodes:
            for f in n.fanin:
                if f not in self._known():
                    raise BlifError("node %s reads %s, which is not an input or a "
                                    "previous node" % (n.name, f))
        return self

    def _known(self):
        if not hasattr(self, "_known_cache"):
            s = set(self.inputs)
            for n in self.nodes:
                s.add(n.name)
            self._known_cache = s
        return self._known_cache

    # -- simulation --------------------------------------------------------
    def pattern(self, n_patterns, seed=0, ones=None):
        """`n_patterns` input patterns, bit-parallel.

        Returns a dict input -> int, where bit p is that input's value under
        pattern p. `ones` caps how many inputs are high per pattern -- the value
        used by the experiment, because uniform random patterns over 256 inputs
        never produce the sparse case that a carry chain needs.
        """
        rng = random.Random(seed)
        if ones is None:
            ones = max(1, self.n_inputs // 4)
        val = {i: 0 for i in self.inputs}
        for p in range(n_patterns):
            picks = rng.sample(self.inputs, min(ones, self.n_inputs))
            bit = 1 << p
            for i in picks:
                val[i] |= bit
        return val

    def mask(self, n_patterns):
        """All-ones over exactly n_patterns bits.

        Not a fixed 4096: the first version used (1<<4096)-1 for constants and
        every constant node then did two 512-byte integer ops per evaluation, on
        a circuit with 1020 nodes. The mask has to match the pattern set.
        """
        return (1 << n_patterns) - 1

    def pattern_exhaustive(self, limit=1 << 16):
        """Every input combination, exactly once, bit-parallel.

        The difference from `pattern` is not efficiency, it is meaning: a sampled
        set of 2048 patterns over an 11-input circuit covers maybe 55 DISTINCT
        input vectors, and calling that "100% of the input space" is the same
        category of error as the one this directory exists to expose. Exhaustive
        means every vector, once, and it is only available when 2^n is small --
        which is exactly when the endpoint can be quoted.
        """
        n = self.n_inputs
        total = 1 << n
        if total > limit:
            raise ValueError("2^%d input vectors exceeds the limit" % n)
        val = {i: 0 for i in self.inputs}
        for p in range(total):
            bit = 1 << p
            for k, name in enumerate(self.inputs):
                if (p >> k) & 1:
                    val[name] |= bit
        return val

    def count_distinct(self, val):
        """How many distinct input vectors a sampled pattern set actually holds.

        Needed because "1000 patterns" and "1000 distinct vectors" are different
        claims and only the second one is a coverage statement.
        """
        names = self.inputs
        seen = set()
        n_bits = max([bin(x).__len__() - 2 for x in val.values()] + [0])
        for p in range(n_bits):
            key = tuple((val[name] >> p) & 1 for name in names)
            seen.add(key)
        return len(seen)

    def evaluate(self, val, fault=None, n_patterns=None):
        """Every wire's bit-parallel value.

        `fault` is (node_name, kind) with kind in {const0, const1, invert} and is
        applied as the node's output is produced, so exactly one connection is
        altered -- the definition of a single stuck-at fault.

        Order matters: BLIF requires each .names to reference only earlier wires,
        and this walks them in file order rather than sorting, so a file that
        is not topologically sorted produces a KeyError instead of a plausible
        wrong answer.
        """
        v = dict(val)
        if n_patterns is None:
            n_patterns = max([bin(x).count("1") for x in v.values()] or [1])
            n_patterns = max(n_patterns, max((bin(x).__len__() - 2) for x in v.values()) if v else 1)
        all_ones = (1 << n_patterns) - 1
        for n in self.nodes:
            if n.is_const is not None:
                out = all_ones if n.is_const else 0
            else:
                # BLIF: the cover is EITHER an on-set or an off-set, and the
                # output column says which. If any row's output is 1, the rows are
                # the on-set and an unmatched input gives 0. If EVERY row's output
                # is 0, the rows are the off-set and an unmatched input gives 1.
                #
                # This module read every cover as an on-set for a long time, which
                # silently turned every all-zero cover into a constant 0 -- 260 of
                # the 260 blocks in int2float, and 1020 of 1020 in adder, all with
                # a single 0-row each. It was found by cross-checking against
                # yosys+iverilog on a benchmark where the outputs have a meaning
                # (int->float: the exponent must be monotone in the input), never
                # by reading the code: `dec` happened to agree because its 0-rows
                # were unreachable given its other blocks.
                hit = 0
                for pat, res in n.cover:
                    sel = all_ones
                    for name, ch in zip(n.fanin, pat):
                        x = v[name]
                        if ch == "1":
                            sel &= x
                        elif ch == "0":
                            sel &= ~x
                        # '-' contributes nothing
                    sel &= all_ones
                    hit |= sel
                out = (all_ones & ~hit) if n.off_set else hit
            if fault and fault[0] == n.name:
                kind = fault[1]
                if kind == "const0":
                    out = 0
                elif kind == "const1":
                    out = ~0
                elif kind == "invert":
                    out = ~out
                else:
                    raise BlifError("unknown fault kind %r" % kind)
            v[n.name] = out
        return v

    def outputs_of(self, v):
        return [v[o] for o in self.outputs]

    def simulate_reference(self, inputs_bits, fault=None):
        """The same circuit, one pattern at a time, as plain Python bools.

        This is the second implementation that makes the bit-parallel one
        believable: a fast simulator checked only against itself is a self-
        consistency check, and if it is wrong every number downstream is wrong in
        the same direction. `tests` compares the two on random patterns and on
        random circuits.
        """
        v = {i: bool(val) for i, val in inputs_bits.items()}
        for n in self.nodes:
            if fault and fault[0] == n.name:
                kind = fault[1]
                if kind == "const0":
                    v[n.name] = False
                    continue
                if kind == "const1":
                    v[n.name] = True
                    continue
            if n.is_const is not None:
                out = n.is_const
            else:
                hit = False
                for pat, _res in n.cover:
                    if all(ch == "-" or v[name] == (ch == "1")
                           for name, ch in zip(n.fanin, pat)):
                        hit = True
                        break
                out = (not hit) if n.off_set else hit
            if fault and fault[0] == n.name and fault[1] == "invert":
                out = not out
            v[n.name] = out
        return {o: v[o] for o in self.outputs}


# --------------------------------------------------------------------------
# faults
# --------------------------------------------------------------------------

def all_faults(net):
    """Every single stuck-at fault, over every wire a fault can be placed on.

    A `const0` and a `const1` on the same wire are different faults and both are
    here. `invert` is included because it is the fault a stuck-at pair can miss:
    a net that is 1 in every pattern it is observed in looks like a stuck-at-1 to
    a pattern set that never sees the other value.
    """
    out = []
    for name in net.inputs:
        out.append((name, "const0"))
        out.append((name, "const1"))
    for n in net.nodes:
        out.append((n.name, "const0"))
        out.append((n.name, "const1"))
        out.append((n.name, "invert"))
    return out


def parse(path):
    model, inputs, outputs, nodes, latches = None, [], [], [], []
    pending = None
    text = open(path, "r", errors="replace").read()
    for line in _expand_continuation_lines(text):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith(".model"):
            model = s.split(None, 1)[1].strip()
        elif s.startswith(".inputs"):
            inputs.extend(s.split()[1:])
        elif s.startswith(".outputs"):
            outputs.extend(s.split()[1:])
        elif s.startswith(".latch"):
            latches.append(s)
        elif s.startswith(".names"):
            parts = s.split()[1:]
            if not parts:
                raise BlifError(".names with no arguments")
            name, fanin = parts[-1], parts[:-1]
            pending = Node(name, fanin, [])
            if not fanin:
                pending.is_const = False
            nodes.append(pending)
        elif s.startswith("."):
            # .end, .exdc, .clock, ... -- none appear in the EPFL set
            continue
        else:
            if pending is None:
                raise BlifError("truth-table row before any .names: %r" % s)
            row = s.split()
            if len(row) == 1 and len(pending.fanin) == 0:
                pending.is_const = (row[0] == "1")
                continue
            if len(row) != 2:
                raise BlifError("bad cover row %r" % s)
            pending.cover.append((row[0], row[1] == "1"))
    net = Netlist(model, inputs, outputs, nodes, latches)
    return net


def load(path):
    return parse(path).load()
