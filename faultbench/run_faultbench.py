"""A fault campaign on the EPFL combinational benchmarks, and the number that
makes a detection rate mean something.

WHY THIS EXISTS

The claim under test is a familiar one: "the gate covered it, the campaign
converged, coverage is high, the design is checked". Every one of those sentences
can be true while the design is broken, and the gap is not small. This measures
the gap on real circuits instead of arguing about it.

THE TWO NUMBERS THAT MATTER

  activation    the faulted circuit differs from the reference SOMEWHERE -- the
                faulted wire or one of its successors holds a different value.
                This is what a coverage database records: a wire toggled, a
                branch was taken, a condition was hit.

  propagation   a PRIMARY OUTPUT differs. This is what "the fault was caught"
                actually requires.

A detector built on activation -- and most coverage numbers are built on
activation -- reports the activation rate as its detection rate. The distance
between the two is the false-positive rate, and it is the size of the lie a
coverage percentage tells.

THE ORACLE IS NOT THE DETECTOR

Whether a fault is observable AT ALL is decided by z3 over the whole circuit, or
by exhaustive enumeration on small ones. The detector never decides whether a
fault was detectable. Without this step a detection rate is a statement about the
fault list: 580 of the 802 faults on int2float are stuck-at faults no output can
ever depend on, and counting those as misses would make any test set look
terrible for a reason that has nothing to do with the test set.

Usage:
    python run_faultbench.py --bench bench/int2float.blif
    python run_faultbench.py --all --patterns 128 --saturation --json results.json
    python run_faultbench.py --all --no-oracle          # fast, no ground truth
"""

import argparse
import json
import os
import sys
import time
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import blif                                                 # noqa: E402
from metrics import compare, per_output_compare, popcount    # noqa: E402

ORACLE_CACHE = os.path.join(HERE, ".oracle_cache.json")


def _cache_load():
    if os.path.exists(ORACLE_CACHE):
        try:
            return json.load(open(ORACLE_CACHE))
        except Exception:
            return {}
    return {}


def _cache_save(d):
    json.dump(d, open(ORACLE_CACHE, "w"), indent=1)


def oracle_verdicts(net, faults, path, timeout_ms=20000, budget_s=None):
    """fault -> 'observable' | 'undetectable-by-construction' | ...

    Cached on disk by benchmark: the SMT answer does not depend on the pattern
    set, which is exactly why it is usable as ground truth for one.
    """
    key = "%s:%d" % (os.path.basename(path), len(net.nodes))
    cache = _cache_load()
    if key in cache:
        have = {tuple(k.split("|")): v for k, v in cache[key].items()}
        if all(f in have for f in faults):
            return {f: have[f] for f in faults}
    import oracle
    verdicts = oracle.resolve_z3(net, faults=faults, timeout_ms=timeout_ms,
                                 budget_s=budget_s)
    cache.setdefault(key, {}).update({"|".join(k): v for k, v in verdicts.items()})
    _cache_save(cache)
    return verdicts


def _activation(ref, under_fault, n_patterns):
    """Did the fault change the value of ANY wire in the circuit?

    A stuck-at on a wire that is itself a don't-care for every pattern will not
    even activate, and that fault is not a miss -- it is not a fault.
    """
    act = 0
    for w, x in ref.items():
        y = under_fault.get(w)
        if y is not None:
            act |= (x ^ y)
    return act & ((1 << n_patterns) - 1)


def campaign(path, n_patterns=128, seed=1, ones=None, use_oracle=True,
             oracle_budget=None, verbose=True, exhaustive=False):
    net = blif.load(path)
    if exhaustive:
        val = net.pattern_exhaustive()
        n_patterns = 1 << net.n_inputs
    else:
        val = net.pattern(n_patterns, seed=seed, ones=ones)
    ref = net.evaluate(val, n_patterns=n_patterns)
    ref_out = net.outputs_of(ref)

    faults = blif.all_faults(net)
    rows = []
    t0 = time.time()
    for name, kind in faults:
        v = net.evaluate(val, fault=(name, kind), n_patterns=n_patterns)
        act = _activation(ref, v, n_patterns)
        prop = compare(ref_out, net.outputs_of(v), n_patterns)
        rows.append({
            "wire": name, "kind": kind,
            "activation_patterns": popcount(act),
            "propagation_patterns": popcount(prop),
            "outputs_affected": len(per_output_compare(ref_out, net.outputs_of(v),
                                                       n_patterns)),
        })
    dt = time.time() - t0

    activated = [r for r in rows if r["activation_patterns"] > 0]
    propagated = [r for r in rows if r["propagation_patterns"] > 0]
    res = {
        "bench": os.path.basename(path),
        "nodes": len(net.nodes),
        "inputs": net.n_inputs,
        "outputs": len(net.outputs),
        "patterns": n_patterns,
        "distinct_patterns": net.count_distinct(val),
        "exhaustive": bool(exhaustive),
        "seed": seed,
        "ones_per_pattern": ones,
        "faults": len(rows),
        "seconds": dt,
        "activated": len(activated),
        "propagated": len(propagated),
        "activation_rate": len(activated) / len(rows) if rows else 0.0,
        "propagation_rate": len(propagated) / len(rows) if rows else 0.0,
        "false_positive_rate": ((len(activated) - len(propagated)) / len(activated)
                                if activated else None),
    }

    if use_oracle:
        verdicts = oracle_verdicts(net, [(r["wire"], r["kind"]) for r in rows],
                                   path, budget_s=oracle_budget)
        for r in rows:
            r["oracle"] = verdicts.get((r["wire"], r["kind"]), "unknown")
        res["oracle"] = dict(Counter(r["oracle"] for r in rows))
        obs = [r for r in rows if r["oracle"] == "observable"]
        caught = [r for r in obs if r["propagation_patterns"] > 0]
        act_obs = [r for r in obs if r["activation_patterns"] > 0]
        res["oracle_observable"] = len(obs)
        res["observable_and_caught"] = len(caught)
        res["escape_rate"] = 1.0 - (len(caught) / len(obs)) if obs else None
        res["observable_false_positive_rate"] = (
            (len(act_obs) - len(caught)) / len(act_obs) if act_obs else None)
        res["impossible_but_caught"] = len(
            [r for r in rows if r["propagation_patterns"] > 0
             and r["oracle"] == "undetectable-by-construction"])

    res["rows"] = rows
    if verbose:
        print("  %-14s %5d nodes %4d in %3d out | %5d faults (%5d distinct patterns)"
              % (res["bench"], res["nodes"], res["inputs"], res["outputs"],
                 res["faults"], res["distinct_patterns"]))
        print("      activation %5d (%.4f)  ->  propagation %4d (%.4f)%s"
              % (res["activated"], res["activation_rate"], res["propagated"],
                 res["propagation_rate"],
                 "" if not res["false_positive_rate"] else
                 "   [%.1f%% of what it called caught was not]"
                 % (100 * res["false_positive_rate"])))
        if use_oracle:
            print("      oracle %s" % res["oracle"])
            if res["escape_rate"] is not None:
                print("      of the %d faults a test COULD catch, this set caught "
                      "%d -- escape %.1f%%"
                      % (res["oracle_observable"], res["observable_and_caught"],
                         100 * res["escape_rate"]))
    return res


def closeness(path, sizes, seed=1, ones=None):
    """Caught-fault count as the pattern set grows, with no oracle attached."""
    out = []
    for n in sizes:
        r = campaign(path, n_patterns=n, seed=seed, ones=ones, use_oracle=False,
                     verbose=False)
        out.append({"patterns": n, "distinct_patterns": r["distinct_patterns"],
                    "propagated": r["propagated"],
                    "propagation_rate": r["propagation_rate"],
                    "detector_says": r["activation_rate"]})
    return out


def saturation(path, oracle_map, max_patterns=4096, seed=1, ones=None,
               exhaustive_limit=1 << 16):
    """Sweep the pattern set size up to the EXHAUSTIVE endpoint, reporting at
    each size what a coverage number would say and what is actually caught.

    The endpoint is the experiment. A complete test set must catch every
    observable fault; if the escape rate is not 0.0 there, the method is wrong.
    And if the escape rate is still large at the size where the activation number
    stopped moving -- which is the size a coverage-driven flow stops at -- that
    is the finding: the flow's stopping rule and the design's correctness are not
    the same question.
    """
    net = blif.load(path)
    full = 1 << net.n_inputs
    history = []

    def record(r, exhaustive):
        obs = [x for x in r["rows"]
               if oracle_map.get((x["wire"], x["kind"])) == "observable"]
        caught = [x for x in obs if x["propagation_patterns"] > 0]
        history.append({
            "patterns": r["patterns"],
            "distinct_patterns": r["distinct_patterns"],
            "exhaustive": exhaustive,
            "pct_of_input_space": (100.0 if exhaustive else
                                   100.0 * r["distinct_patterns"] / full),
            "activated": r["activated"],
            "activation_rate": r["activation_rate"],
            "propagated": r["propagated"],
            "propagation_rate": r["propagation_rate"],
            "observable": len(obs),
            "caught": len(caught),
            "escape_rate": 1.0 - (len(caught) / len(obs)) if obs else None,
        })

    n = 1
    cap = min(max_patterns, full)
    while n <= cap:
        r = campaign(path, n_patterns=n, seed=seed, ones=ones, use_oracle=False,
                     verbose=False)
        record(r, False)
        if n == full:
            break
        n *= 2
    if full <= exhaustive_limit and (not history or not history[-1]["exhaustive"]):
        record(campaign(path, use_oracle=False, verbose=False, exhaustive=True), True)
    return history


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--bench", action="append", default=[])
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--patterns", type=int, default=128)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--ones", type=int, default=None,
                    help="inputs high per pattern (default: n_inputs/4)")
    ap.add_argument("--no-oracle", action="store_true")
    ap.add_argument("--oracle-budget", type=float, default=None,
                    help="seconds allowed for the SMT oracle per benchmark")
    ap.add_argument("--closeness", action="store_true")
    ap.add_argument("--saturation", action="store_true")
    ap.add_argument("--json")
    args = ap.parse_args(argv)

    bench_dir = os.path.join(HERE, "bench")
    paths = list(args.bench)
    if args.all:
        paths = [os.path.join(bench_dir, f) for f in sorted(os.listdir(bench_dir))
                 if f.endswith(".blif")]
    if not paths:
        ap.error("give --bench <file> or --all")

    out = {"patterns": args.patterns, "seed": args.seed, "runs": [], "closeness": {},
           "saturation": {}}
    maps = {}
    for p in paths:
        r = campaign(p, args.patterns, args.seed, ones=args.ones,
                     use_oracle=not args.no_oracle, oracle_budget=args.oracle_budget)
        out["runs"].append(r)
        if not args.no_oracle:
            maps[os.path.basename(p)] = {(x["wire"], x["kind"]): x["oracle"]
                                         for x in r["rows"]}
        if args.closeness:
            out["closeness"][os.path.basename(p)] = closeness(
                p, [8, 16, 32, 64, 128, 256, 512], seed=args.seed, ones=args.ones)
        if args.saturation:
            out["saturation"][os.path.basename(p)] = saturation(
                p, maps.get(os.path.basename(p), {}), seed=args.seed, ones=args.ones)
    for r in out["runs"]:
        r.pop("rows", None)          # JSON stays readable; the summary is the point
    if args.json:
        json.dump(out, open(args.json, "w"), indent=2)
        print("wrote %s" % args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
