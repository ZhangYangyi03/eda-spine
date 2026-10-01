"""Cross-check the fast simulator against a completely different toolchain.

`blif.py` and `oracle.py` share the netlist reader, so agreement between them
does not rule out a shared misreading of BLIF. This does: it writes the same
benchmark out as Verilog with yosys, enumerates every input vector in a testbench
run by Icarus Verilog, and compares the primary outputs against the bit-parallel
simulator, vector by vector.

Only runs on benchmarks with few enough inputs for 2^n vectors to be enumerated.
The point is not coverage of the benchmark set; it is that the number this
directory reports does not rest on one piece of code reading one format.

    python crosscheck_iverilog.py --bench bench/dec.blif
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import blif                                        # noqa: E402


def run(cmd, cwd):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, timeout=600)
    if r.returncode != 0:
        raise RuntimeError("%s failed:\n%s\n%s"
                           % (" ".join(cmd), r.stdout.decode(errors="replace"),
                              r.stderr.decode(errors="replace")))
    return r.stdout.decode(errors="replace")


def yosys_to_verilog(blif_path, out_v, workdir):
    # absolute paths: yosys runs with cwd=workdir, so a bare basename looks for
    # the benchmark in the scratch directory and finds nothing
    src = os.path.abspath(blif_path)
    dst = os.path.abspath(out_v)
    run(["yosys", "-q", "-p",
         "read_blif %s; hierarchy -auto-top; proc; opt; write_verilog -noattr %s"
         % (src, dst)], workdir)


def parse_module_ports(verilog_text):
    """The module's port list, in order, as RAW tokens.

    Yosys writes these benchmarks with escaped identifiers -- `\\count[0]`, brackets
    included -- because `count[0]` is a bus named `count` written as `count[0]`,
    and `\\count[0]` is a single net literally named `count[0]`. They are different
    things and the escaping is yosys being correct. A testbench that connects
    `.count_0(...)` therefore fails elaboration with 264 errors, which is how this
    was found.

    The raw token is kept because that is what has to go back into the port
    connection. `decode` gives the name to compare against the BLIF file.
    """
    m = re.search(r"\bmodule\s+(\w+)\s*\((.*?)\)\s*;", verilog_text, re.S)
    if not m:
        raise RuntimeError("no module port list found")
    top, raw = m.group(1), m.group(2)
    tokens = []
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if chunk.startswith("\\"):
            # an escaped identifier ends at the first whitespace
            tokens.append(chunk.split()[0])
        else:
            tokens.append(chunk)
    return top, tokens


def decode_port(tok):
    return tok[1:] if tok.startswith("\\") else tok


def testbench(top, in_ports, out_ports):
    conns = []
    for k, tok in enumerate(in_ports):
        conns.append(".%s (pat[%d])" % (tok, k))
    for k, tok in enumerate(out_ports):
        conns.append(".%s (obs[%d])" % (tok, k))
    return "\n".join([
        "`timescale 1ns/1ps",
        "module tb;",
        "  reg [%d:0] pat;" % (len(in_ports) - 1),
        "  wire [%d:0] obs;" % (len(out_ports) - 1),
        "  integer i;",
        "  %s dut(" % top,
        "    " + ",\n    ".join(conns),
        "  );",
        "  initial begin",
        "    for (i = 0; i < %d; i = i + 1) begin" % (1 << len(in_ports)),
        "      pat = i; #1;",
        '      $display("%0d %b", i, obs);',
        "    end",
        "    $finish;",
        "  end",
        "endmodule",
    ]) + "\n"


def crosscheck(path):
    net = blif.load(path)
    if net.n_inputs > 16:
        raise ValueError("%d inputs -- too many to enumerate" % net.n_inputs)
    work = tempfile.mkdtemp(prefix="fbcross_")
    try:
        v = os.path.join(work, "dut.v")
        yosys_to_verilog(path, v, work)

        top, ports = parse_module_ports(open(v).read())
        if len(ports) != net.n_inputs + len(net.outputs):
            raise RuntimeError("module has %d ports, blif has %d in + %d out"
                               % (len(ports), net.n_inputs, len(net.outputs)))
        in_ports = ports[:net.n_inputs]
        out_ports = ports[net.n_inputs:]
        # the naming must agree after decoding, or this cross-check is comparing
        # two different circuits that happen to share a port count
        got_in = [decode_port(x) for x in in_ports]
        got_out = [decode_port(x) for x in out_ports]
        if got_in != net.inputs or got_out != net.outputs:
            raise RuntimeError("port names disagree with the blif file:\n  blif: %s\n  yosys: %s"
                               % (net.inputs[:4], got_in[:4]))

        tb = testbench(top, in_ports, out_ports)
        tbp = os.path.join(work, "tb.v")
        open(tbp, "w").write(tb)
        exe = os.path.join(work, "simv")
        run(["iverilog", "-o", exe, v, tbp], work)
        out = run([exe], work)

        # collect iverilog's per-vector outputs
        hw = {}
        for line in out.splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[0].isdigit():
                p = int(parts[0])
                bits = parts[1].zfill(len(net.outputs))
                # obs[0] is the LAST character of $display's %b
                hw[p] = {o: (bits[len(bits) - 1 - k] == "1")
                         for k, o in enumerate(net.outputs)}

        val = net.pattern_exhaustive()
        ref = net.evaluate(val, n_patterns=1 << net.n_inputs)
        mismatches = []
        for p in range(1 << net.n_inputs):
            for k, o in enumerate(net.outputs):
                mine = bool((ref[o] >> p) & 1)
                if mine != hw[p][o]:
                    mismatches.append((p, o, mine, hw[p][o]))
        return {"bench": os.path.basename(path), "vectors": 1 << net.n_inputs,
                "outputs": len(net.outputs), "top": top,
                "mismatches": len(mismatches), "examples": mismatches[:5]}
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bench", action="append", default=[])
    ap.add_argument("--all-small", action="store_true")
    args = ap.parse_args()
    bench_dir = os.path.join(HERE, "bench")
    paths = args.bench
    if args.all_small:
        for f in sorted(os.listdir(bench_dir)):
            if f.endswith(".blif"):
                n = blif.load(os.path.join(bench_dir, f))
                if n.n_inputs <= 16:
                    paths.append(os.path.join(bench_dir, f))
    if not paths:
        ap.error("give --bench <file> or --all-small")
    bad = 0
    for p in paths:
        r = crosscheck(p)
        flag = "agree" if r["mismatches"] == 0 else "DISAGREE"
        print("  %-16s %5d vectors x %3d outputs -> %s (%d mismatches)"
              % (r["bench"], r["vectors"], r["outputs"], flag, r["mismatches"]))
        if r["mismatches"]:
            bad += 1
            for e in r["examples"]:
                print("      vector %d output %s: blif.py=%s iverilog=%s" % e)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
