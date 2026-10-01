"""The three things this experiment produces, and what each one is for.

A fault campaign has a vocabulary and it is easy to produce numbers that look
rigorous and mean nothing. These are the three:

    detection rate    of the faults injected, the fraction the test set caught.
                      A low number is not automatically bad -- it is only bad
                      relative to what the fault set contains (see below).

    false-positive    of the faults the detector SIGNALS as caught, the fraction
    rate              that were not actually caught. This is the number worth
                      watching, because it is the one that hides a broken
                      detector: a detector that signals everything has a
                      detection rate of 1.0 and a false-positive rate of
                      (1 - true detection rate).

    escape rate       of the faults that PROVABLY change the output, the
                      fraction this pattern set does not see. This is the only
                      honest measure of test quality, and it is the one a
                      coverage percentage silently replaces. Computed here with
                      exhaustive input enumeration on a small circuit, or with
                      the difference set on the sampled patterns otherwise.

The distinction that makes the experiment worth running: a fault set contains
faults that are UNDETECTABLE BY CONSTRUCTION (a stuck-at on a wire no output
depends on, a wire no pattern can distinguish). Reporting a detection rate
without removing those is arithmetic, not measurement. So every fault is
cross-checked against a second, independent oracle, and the numbers are reported
over the faults that oracle says are observable.
"""

import collections


def compare(reference, under_fault, n_patterns):
    """Bit-parallel difference: which patterns see a difference on any output."""
    diff = 0
    for r, f in zip(reference, under_fault):
        diff |= (r ^ f)
    full = (1 << n_patterns) - 1
    return diff & full


def per_output_compare(reference, under_fault, n_patterns):
    """Which outputs differ, per pattern. Needed because "the output is wrong"
    and "the wrong output is wrong" are different findings for a fault that is
    only observable on one of 32 sum bits."""
    full = (1 << n_patterns) - 1
    per = {}
    for i, (r, f) in enumerate(zip(reference, under_fault)):
        d = (r ^ f) & full
        if d:
            per[i] = d
    return per


def popcount(x):
    return bin(x).count("1")
