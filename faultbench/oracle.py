"""The independent oracle: is this fault observable at all?

This is the module that makes every other number in faultbench quotable. A fault
campaign that measures its own fault list reports a detection rate that is a
property of the fault list. Here the observable/undetectable split is decided by
an SMT solver over the whole circuit, against the same netlist the simulator
reads but through a completely separate execution path.

Two oracles, because they fail differently:

    resolve_exhaustive   small input space: every input pattern, all at once.
                         Exact, and the only one that can be trusted absolutely,
                         because it is the definition of the property.
    resolve_z3           large input space: ask z3 whether an input vector exists
                         that distinguishes the faulted circuit from the
                         reference. Exact too, but it is a solver, so it can
                         answer "unknown" and that answer is kept as unknown
                         rather than folded into either side.

The faults neither oracle can observe are the ones that must be REMOVED from the
denominator before a detection rate means anything: a stuck-at on a wire no
output depends on can never be detected by any test, and counting it as a miss
would make every test set look bad for a reason that has nothing to do with the
test set.
"""

import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import blif                                       # noqa: E402


def resolve_exhaustive(net, faults=None, limit_patterns=1 << 16):
    """Ground truth by enumerating the input space. Refuses when it is too big.

    This version does not reuse the bit-parallel simulator: for each pattern it
    calls `simulate_reference`, the plain-bool implementation, for the reference
    and for the faulted circuit. Slow and independent on purpose. It is the
    definition of observability being executed, not an approximation of it.
    """
    n = net.n_inputs
    if (1 << n) > limit_patterns:
        raise ValueError("2^%d patterns exceeds the limit -- use resolve_z3" % n)
    ref = {}
    for p in range(1 << n):
        bits = {name: bool((p >> i) & 1) for i, name in enumerate(net.inputs)}
        ref[p] = net.simulate_reference(bits)
    out = {}
    for f in (faults if faults is not None else blif.all_faults(net)):
        seen = 0
        for p in range(1 << n):
            bits = {name: bool((p >> i) & 1) for i, name in enumerate(net.inputs)}
            got = net.simulate_reference(bits, fault=f)
            if any(got[o] != ref[p][o] for o in net.outputs):
                seen += 1
        out[f] = seen
    return out


def resolve_z3(net, faults=None, timeout_ms=20000, budget_s=None):
    """Ground truth by SMT. Returns fault -> number of distinguishing patterns,
    or None when the solver said unknown."""
    import z3

    xs = {name: z3.Bool("in_%s" % name.replace("[", "_").replace("]", ""))
          for name in net.inputs}

    def build(fault):
        v = dict(xs)
        for node in net.nodes:
            if node.is_const is not None:
                out = z3.BoolVal(node.is_const)
            else:
                terms = []
                for pat, res in node.cover:
                    conj = []
                    for name, ch in zip(node.fanin, pat):
                        if ch == "1":
                            conj.append(v[name])
                        elif ch == "0":
                            conj.append(z3.Not(v[name]))
                    terms.append(z3.And(*conj) if conj else z3.BoolVal(True)
                                 if res else z3.BoolVal(False))
                pos = z3.Or(*[t for t, (_p, r) in zip(terms, node.cover) if r]) \
                    if any(r for _p, r in node.cover) else z3.BoolVal(False)
                out = pos
            if fault and fault[0] == node.name:
                if fault[1] == "const0":
                    out = z3.BoolVal(False)
                elif fault[1] == "const1":
                    out = z3.BoolVal(True)
                elif fault[1] == "invert":
                    out = z3.Not(out)
            v[node.name] = out
        return [v[o] for o in net.outputs]

    good = build(None)
    results = {}
    t0 = time.time()
    for f in (faults if faults is not None else blif.all_faults(net)):
        if budget_s is not None and (time.time() - t0) > budget_s:
            # A budget, and the faults it did not reach are 'unknown' rather than
            # quietly dropped or guessed. A campaign that reports a rate over a
            # partially-resolved oracle without saying so is the failure mode
            # this whole file exists to avoid.
            results[f] = "unknown-budget-exhausted"
            continue
        bad = build(f)
        s = z3.Solver()
        s.set("timeout", timeout_ms)
        s.add(z3.Or(*[a != b for a, b in zip(good, bad)]))
        r = s.check()
        if r == z3.sat:
            results[f] = "observable"
        elif r == z3.unsat:
            results[f] = "undetectable-by-construction"
        else:
            results[f] = "unknown"
    return results
