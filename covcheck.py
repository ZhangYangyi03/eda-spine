"""covcheck: the coverage half of the spine.

The seam stage proves a property of the synthesised netlist. This one asks a
different question about the same file -- and the pair is the point, because the
two questions are answered by different tools that share nothing but the gate
netlist:

    formal      "is there a trace where the netlist disobeys the property?"
    coverage    "does every bit of the output ever move under stimulus?"

Neither one is redundant. A dead output bit can be proved consistent with a
property that never mentions that bit, and a wrong value on a live bit can pass
coverage with flying colours.

The reading of the raw database is covagent's, not a reimplementation:
covagent.boundary.parse_dump / toggles / bits_of / profile_str. Verilator's own
--write-info drops every toggle bin (measured: 27 line records, 0 toggle
records), which is exactly the bug that makes the lcov summary useless for this.

Verilator's generated main never calls coveragep()->write(), so the coverage
database is thrown away -- sim_main.cpp here is covagent's own replacement main,
copied from its bench, and it is why the numbers are real.
"""

import base64
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "covagent"))
from covagent import boundary                      # noqa: E402
from covagent.sim import win_to_wsl                # noqa: E402

COVAGENT = os.path.join(os.path.dirname(HERE), "covagent")


def _bash(script, timeout):
    b64 = base64.b64encode(script.encode("utf-8")).decode("ascii")
    cmd = 'wsl -d Ubuntu -- bash -lc "echo %s | base64 -d > /tmp/edaspine.sh && bash /tmp/edaspine.sh"' % b64
    r = subprocess.run(cmd, shell=True, capture_output=True, timeout=timeout)
    return (r.stdout or b"").decode("utf-8", "replace"), r.returncode


def build_and_run(files, tb_top, workdir, timeout=600):
    """Verilate, build, run, and write both the raw dump and the lcov report."""
    os.makedirs(workdir, exist_ok=True)
    shutil.copy(os.path.join(COVAGENT, "bench", "rtl", "sim_main.cpp"),
                os.path.join(workdir, "sim_main.cpp"))
    vt = "V%s" % tb_top
    with open(os.path.join(workdir, "cfg.h"), "w", encoding="utf-8", newline="\n") as f:
        f.write('#define VTOP_HEADER "%s.h"\n#define VTOP %s\n#define COVERAGE_FILE "cov.dat"\n'
                % (vt, vt))
    mount = win_to_wsl(workdir)
    names = " ".join(os.path.basename(f) for f in files)
    script = ("cd %s || exit 7\n"
              "rm -rf obj_dir cov.dat coverage.info\n"
              "verilator --cc --timing --coverage --coverage-line --coverage-toggle "
              "-Wno-fatal -Wno-INITIALDLY --top-module %s %s sim_main.cpp "
              "-Mdir obj_dir -o sim --exe > v.log 2>&1 || { tail -20 v.log; exit 8; }\n"
              "make -C obj_dir -f %s.mk -j4 >> v.log 2>&1 || { tail -20 v.log; exit 9; }\n"
              "./obj_dir/sim > sim.log 2>&1\n"
              "verilator_coverage --write-info coverage.info cov.dat 2>&1 | tail -2\n"
              % (mount, tb_top, names, vt))
    out, rc = _bash(script, timeout)
    return {"dump": os.path.join(workdir, "cov.dat"),
            "info": os.path.join(workdir, "coverage.info"),
            "log": out, "rc": rc}


def output_toggles(dump_path, top, names):
    """Per-bit toggle counts of `names`, read with covagent's own parser."""
    tog = boundary.toggles(dump_path)
    prof = {}
    for n in names:
        prof[n] = boundary.bits_of(tog, top, n)
    return tog, prof


def dead_bits(prof):
    """{name: [bit, ...]} for bits that never toggled. None = the whole vector."""
    dead = {}
    for name, bybit in prof.items():
        bits = [b for b, c in bybit.items() if b is not None and c == 0]
        if bits:
            dead[name] = sorted(bits)
        elif bybit.get(None, 0) == 0 and not [b for b in bybit if b is not None]:
            dead[name] = [None]
    return dead
