"""The chain is a gate, so it is tested the way a gate is tested: give it
something it must reject.

These tests do not run the toolchain. They test the parts that decide what the
verdict means -- and each one exists because it was wrong at least once while
this was being written.
"""

import os
import sys

import pytest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import spine                                       # noqa: E402


def test_mutants_refuse_to_invent_a_verdict():
    """A netlist whose shape changed must stop the run, not silently mutate nothing."""
    with pytest.raises(SystemExit):
        spine._mutants("counter", "module m; endmodule  // nothing to edit here" + chr(10))


def test_mutants_are_per_design():
    """The counter's edits must not be applied to the FSM's netlist."""
    net = open(os.path.join(HERE, "..", "assertforge", "bench", "designs",
                            "counter.v"), encoding="utf-8").read()
    assert "stuck_bit0" in spine._mutants("counter", net + "\nassign _0_[0] = 2'h1;")


def test_unknown_design_is_refused():
    with pytest.raises(SystemExit):
        spine.run("nope.v", "nope", "nope.sv", design="not_a_design")


def _code_only(text):
    """The module with its comments stripped, so a comment ABOUT the failed
    control does not read as the failed control."""
    return "\n".join(l.split("//")[0] for l in text.splitlines())


def test_false_control_is_not_the_property():
    """The control must be a DIFFERENT property, and it must be one the gate can
    reject. The control that failed first was the property with `assume (rst)`
    dropped -- it came back PROVED on a clean design, i.e. it could not fail."""
    for design, text in spine.FALSE_CONTROL.items():
        code = _code_only(text)
        assert "assume" not in code, design
        assert "assert" in code, design
        # the control must NOT carry the guards whose removal makes it false
        assert "&& !prst" not in code and "&& !prst)" not in code, design


def test_every_tabulated_design_is_complete():
    """A design in TB but not in WATCH/MUTANTS/FALSE_CONTROL is a half-added
    case, and the failure would show up as a KeyError mid-run instead."""
    for d in spine.TB:
        assert d in spine.WATCH, d
        assert d in spine.MUTANTS and spine.MUTANTS[d], d
        assert d in spine.FALSE_CONTROL, d
        assert d in spine.COVERAGE_EXPECTS_DEAD, d


def test_coverage_expectation_names_real_mutants():
    for d, names in spine.COVERAGE_EXPECTS_DEAD.items():
        for n in names:
            assert n in spine.MUTANTS[d], (d, n)
