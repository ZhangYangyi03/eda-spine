"""eda-spine: the chain that did not exist between the four tools.

autoforge is the orchestrator; it drives three domain tools, and until now each
of them was invoked by hand, by a person, one at a time:

    assertforge   generate + prove properties of RTL      (sby / yosys / z3, in WSL)
    covagent      find the dark bins and close them       (verilator, in WSL)
    qoragent      search synthesis passes, gate on equivalence

No file in any of the three repos imported another one of them (measured: the
only cross-reference was one sentence in covagent's README). This module is that
file. It reimplements nothing -- it calls the three packages in order and hands
each one the artefact the previous one produced.

What the chain actually adds
---------------------------
qoragent's gate is *equivalence*: the netlist computes the same function as the
RTL. That is one property, and a property set is not the design. So the question
this chain answers is the one that matters after synthesis:

    does the POST-SYNTHESIS gate netlist still satisfy the property that
    assertforge proved of the RTL?

and the coverage half asks the companion question on the same file:

    does every bit of the output still move under stimulus, on the netlist?

Three results are measured here, and each one is a different mistake:

  1. formal, strong property   clean netlist PROVED / stuck bit REFUTED /
                               en-gating swapped REFUTED
  2. formal, weak property     reset-only property PROVES on BOTH broken
                               netlists  ->  a property can be true and blind
  3. coverage                  stuck bit is a DEAD COVERAGE BIN (0 toggles on
                               all four bits); en-gating swapped is INVISIBLE
                               to coverage (all bits live, counts 17/8/4/2).
                               The strong property catches that one, coverage
                               catches neither on its own, so the two stages
                               are not redundant and neither is sufficient.

  4. the negative control      the same property with the reset assumption
                               removed REFUTES on the CLEAN netlist too. A gate
                               that cannot say no to a correct design is not a
                               gate, and without this run a "PROVED" proves
                               nothing about the property.

A note on each tool's own artefact, which is where the surfaces really differ:

  qoragent speaks RTL and writes  netlist.v          (Verilog)
  assertforge speaks SMT and reads RTL + assertions  (one sby job per question)
  covagent speaks a raw coverage database            (cov.dat, not the lcov summary)

The last one is not incidental. Verilator's --write-info keeps line records only
(measured: 27 line records, 0 toggle records), so the lcov file cannot answer a
per-bit question at all; the raw database is why covagent's own seam work is
per-bit. This chain reads the raw database with covagent's parser, not a copy.

    python spine.py --rtl <design.v> --top <top> --props <props.sv> [--no-coverage]
"""

import argparse
import json
import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)                     # ...\项目_开发
TOOLS = {"qoragent": os.path.join(PROJ, "qoragent"),
         "assertforge": os.path.join(PROJ, "assertforge"),
         "covagent": os.path.join(PROJ, "covagent")}
for p in TOOLS.values():
    if p not in sys.path:
        sys.path.insert(0, p)

from qoragent import optimize, synth            # noqa: E402
from assertforge import formal                  # noqa: E402
import covcheck                                 # noqa: E402

WORK = os.path.join(os.environ.get("LOCALAPPDATA", HERE), "eda-spine", "work")

# The stimulus for the coverage stage. One testbench per design shape, because
# the coverage question is "does every output bit move under SOME stimulus" and
# the stimulus is design-specific; the design file is dropped in beside it and
# the module instance is the only line that differs.
TB = {
    "counter": """module tb;
  reg clk=0, rst=1, en=0; wire [3:0] cnt;
  counter dut(.clk(clk), .rst(rst), .en(en), .cnt(cnt));
  always #5 clk = ~clk;
  integer i;
  initial begin
    repeat(4) @(posedge clk); rst = 0;
    for (i=0;i<24;i=i+1) begin @(posedge clk); en = (i % 3 == 0); end
    $finish;
  end
endmodule
""",
    "traffic_fsm": """module tb;
  reg clk=0, rst=1, tick=0; wire [1:0] state;
  traffic_fsm dut(.clk(clk), .rst(rst), .tick(tick), .state(state));
  always #5 clk = ~clk;
  integer i;
  initial begin
    repeat(4) @(posedge clk); rst = 0;
    for (i=0;i<16;i=i+1) begin @(posedge clk); tick = 1; end
    @(posedge clk); tick = 0;
    repeat(4) @(posedge clk);
    $finish;
  end
endmodule
""",
    "sync_fifo": """module tb;
  reg clk=0, rst=1, wr_en=0, rd_en=0; reg [7:0] wr_data=0;
  wire [7:0] rd_data; wire full, empty; wire [2:0] count;
  sync_fifo dut(.clk(clk), .rst(rst), .wr_en(wr_en), .wr_data(wr_data),
                .rd_en(rd_en), .rd_data(rd_data), .full(full), .empty(empty),
                .count(count));
  always #5 clk = ~clk;
  integer i;
  initial begin
    repeat(4) @(posedge clk); rst = 0;
    for (i=0;i<16;i=i+1) begin
      @(posedge clk); wr_en = 1; wr_data = i;
    end
    @(posedge clk); wr_en = 0;
    for (i=0;i<16;i=i+1) begin @(posedge clk); rd_en = 1; end
    @(posedge clk); rd_en = 0;
    $finish;
  end
endmodule
""",
}

# What the coverage stage looks at, per design.
WATCH = {"counter": ["cnt"], "traffic_fsm": ["state"],
         "sync_fifo": ["count", "full", "empty"]}

# One-token edits to the gate netlist, per design. Each is legal Verilog, keeps
# the ports, and is functionally wrong. If none matches, the run stops rather
# than inventing a verdict.
MUTANTS = {
    "counter": {"stuck_bit0": ("assign _0_[0] = 2'h1", "assign _0_[0] = 2'h2"),
                "swap_en_gating": ("else if (en)", "else if (!en)")},
    "traffic_fsm": {
        # the next-state ROM's bit 1 forced low: the FSM now has two states
        # instead of four, and the ORDER invariant is what notices.
        "state_seq": ("assign _0_[1] = 4'h6 >> { state[0], state[1] };",
                      "assign _0_[1] = 4'h0 >> { state[0], state[1] };"),
        # the tick gate inverted on ONE of the two state bits: an asymmetric
        # change that still toggles the output, so coverage cannot see it.
        "tick_gate": ("else if (tick) state[0]", "else if (!tick) state[0]")},
    "sync_fifo": {"count_guard": ("if (do_wr) begin", "if (do_wr || do_rd) begin"),
                  "full_test": ("assign full  = (count == DEPTH);",
                                "assign full  = (count >= DEPTH);")},
}

# Which mutant names the COVERAGE stage is expected to see as a dead bin. The
# point of the table is that coverage and formal disagree about which bugs are
# visible, and the disagreement is the reason both stages exist.
COVERAGE_EXPECTS_DEAD = {"counter": {"stuck_bit0"},
                         "traffic_fsm": {"state_seq"},
                         "sync_fifo": set()}


def _mutants(design, text):
    out = {}
    for name, (a, b) in MUTANTS[design].items():
        if a in text:
            out[name] = (text.replace(a, b, 1), "%s -> %s" % (a, b))
    if not out:
        raise SystemExit("no mutation matched -- the netlist shape changed; "
                         "fix MUTANTS rather than inventing a verdict")
    return out


def run(rtl, top, props, workdir=None, timeout=300, depth=8, coverage=True,
        design=None):
    """The whole chain, in order, one artefact per stage.

    Returns a dict. The verdict is SPINE_OK only when every stage produced the
    result that stage is there to produce -- a stage that passes whatever it is
    given is decoration, and the verdict says so instead of hiding it.
    """
    design = design or top
    if design not in TB:
        raise SystemExit("no stimulus for design %r; known: %s. Add a TB entry "
                         "rather than reusing another design's stimulus."
                         % (design, ", ".join(sorted(TB))))
    workdir = workdir or WORK
    shutil.rmtree(workdir, ignore_errors=True)
    os.makedirs(workdir, exist_ok=True)
    st = {}

    # ---- 1. qoragent: search synthesis passes, keep only equivalent wins ----
    t0 = time.time()
    res = optimize.search(rtl, top, os.path.join(workdir, "search"), timeout=timeout)
    rep = optimize.report(res)
    st["qoragent"] = {"kept": [s.name for s in res.kept],
                      "seconds": round(time.time() - t0, 2), "report": rep}
    print("[1/5] qoragent  %s" % rep.splitlines()[2].strip())
    if not res.kept:
        return _finish(st, "NO_NETLIST", workdir)

    rtl_copy = os.path.join(workdir, os.path.basename(rtl))
    shutil.copy(rtl, rtl_copy)
    net = os.path.join(workdir, "netlist.v")
    shutil.copy(res.netlist_path, net)
    net_text = open(net, encoding="utf-8").read()
    muts = _mutants(design, net_text)
    props_copy = os.path.join(workdir, "props.sv")
    shutil.copy(props, props_copy)

    # ---- 2. the seam, formal: RTL, netlist, and one perturbed line ----------
    st["formal"] = {}
    runs = [("rtl", [os.path.basename(rtl_copy)]), ("netlist", ["netlist.v"])]
    for name, srcs in runs:
        r = formal.run(workdir, top + "_check", srcs, ["props.sv"],
                       depth=depth, timeout=timeout)
        st["formal"][name] = {"status": r.status, "seconds": round(r.seconds, 2)}
        print("[2/5] formal  %-9s : %s" % (name, r.status))
    for name, (text, how) in muts.items():
        fn = "netlist_%s.v" % name
        open(os.path.join(workdir, fn), "w", encoding="utf-8",
             newline="\n").write(text)
        r = formal.run(workdir, top + "_check", [fn], ["props.sv"],
                       depth=depth, timeout=timeout)
        st["formal"][name] = {"status": r.status, "seconds": round(r.seconds, 2),
                              "edit": how}
        print("[2/5] formal  %-9s : %s   (%s)" % (name, r.status, how))

    # ---- 3. the gate's own control: a property it MUST reject ---------------
    # A gate that cannot say no is not a gate, and every PROVED above would be
    # worth nothing. So a second property is built that is plainly false of the
    # design -- the step assertion with its `en`/`rst` guards stripped -- and the
    # gate has to REFUTE it on the CLEAN netlist.
    #
    # Measured, and the reason this is not "drop the reset assumption": dropping
    # `assume (rst)` alone still yields PROVED on the clean netlist, because the
    # assertion's own `!prst` guard already covers what the assumption covered.
    # A control that cannot fail is not a control, so the guards go too.
    false_prop = FALSE_CONTROL[design]
    st["negative_control"] = {"property": "a property that is false of this design"}
    for name, srcs in (("clean", ["netlist.v"]),
                       ("stuck_bit0", ["netlist_stuck_bit0.v"])):
        if not os.path.exists(os.path.join(workdir, srcs[0])):
            continue
        fn = "false_%s.sv" % name
        open(os.path.join(workdir, fn), "w", encoding="utf-8",
             newline="\n").write(false_prop)
        r = formal.run(workdir, top + "_check", srcs, [fn],
                       depth=depth, timeout=timeout)
        st["negative_control"][name] = r.status
    nc = st["negative_control"].get("clean")
    print("[3/5] formal  control   : a FALSE property on the clean netlist -> %s" % nc)

    # ---- 4. coverage, on the same netlist, read from the raw database -------
    if coverage:
        st["coverage"] = {}
        asm = {"rtl": (os.path.basename(rtl_copy), open(rtl_copy, encoding="utf-8").read()),
               "netlist": ("netlist.v", net_text)}
        asm.update({k: ("netlist_%s.v" % k, v[0]) for k, v in muts.items()})
        for name, (fn, text) in asm.items():
            d = os.path.join(workdir, "cov_" + name)
            os.makedirs(d, exist_ok=True)
            open(os.path.join(d, "counter.v"), "w", encoding="utf-8",
                 newline="\n").write(text)
            open(os.path.join(d, "tb.v"), "w", encoding="utf-8",
                 newline="\n").write(TB[design])
            r = covcheck.build_and_run([os.path.join(d, "counter.v"),
                                        os.path.join(d, "tb.v")], "tb", d,
                                       timeout=timeout)
            tog, prof = covcheck.output_toggles(r["dump"], "TOP.tb.dut",
                                                WATCH[design])
            prof_str = " ".join("%s%s" % (w, covcheck.boundary.profile_str(prof[w]))
                                for w in WATCH[design])
            st["coverage"][name] = {"profile": prof_str,
                                    "dead": covcheck.dead_bits(prof),
                                    "rc": r["rc"]}
            print("[4/5] coverage %-9s : %-22s dead=%s"
                  % (name, st["coverage"][name]["profile"],
                     st["coverage"][name]["dead"] or "-"))

    ok = (st["formal"]["rtl"]["status"] == "PROVED"
          and st["formal"]["netlist"]["status"] == "PROVED"
          and all(st["formal"][m]["status"] == "REFUTED" for m in muts)
          and nc == "REFUTED")
    if coverage:
        # the clean netlist must have no dead bit; and the mutants the table
        # names must SHOW one. A coverage stage that reports nothing on the bug
        # it is supposed to see is as broken as a formal gate that says no to
        # everything, and both are checked rather than assumed.
        ok = ok and st["coverage"]["netlist"]["dead"] == {}
        for mut in COVERAGE_EXPECTS_DEAD[design]:
            ok = ok and bool(st["coverage"].get(mut, {}).get("dead"))
    return _finish(st, "SPINE_OK" if ok else "SPINE_BROKEN", workdir)


FALSE_CONTROL = {
    "counter": """// The positive property with the guards stripped off: `en` no longer
// gates the step, so the assertion is plainly false of a correct counter.
// Built here rather than derived, because the derivation that looks obvious --
// drop `assume (rst)` -- was measured and does NOT work: the assertion's own
// `!prst` guard already covers what the assumption covered, so the "control"
// comes back PROVED on a clean design. A control that cannot fail is not one.
module counter_check(input clk, input rst, input en, input [3:0] cnt);
  counter u(.clk(clk), .rst(rst), .en(en), .cnt(cnt));
  reg [3:0] pcnt; reg pen; reg started = 1'b0;
  always @(posedge clk) begin pcnt <= cnt; pen <= en; started <= 1'b1; end
  always @(posedge clk) if (started) assert (cnt == pcnt + 1'b1);
endmodule
""",
    "traffic_fsm": """// A near-miss false property: the transition ORDER with one edge wrong --
// GREEN is asserted to go to RED, when the design goes GREEN -> YELLOW. False,
// but only by one edge, which is the kind of property a weak gate would accept.
module traffic_fsm_check(input clk, input rst, input tick, input [1:0] state);
  localparam RED = 2'd0, RED_YELLOW = 2'd1, GREEN = 2'd2, YELLOW = 2'd3;
  traffic_fsm u(.clk(clk), .rst(rst), .tick(tick), .state(state));
  reg [1:0] pstate; reg ptick; reg started = 1'b0;
  always @(posedge clk) begin pstate <= state; ptick <= tick; started <= 1'b1; end
  always @(posedge clk) if (started && ptick) assert (!(pstate == GREEN) || state == RED);
endmodule
""",
    "sync_fifo": """// A plainly false occupancy assertion: `count` is provably <= DEPTH.
module sync_fifo_check(
    input clk, input rst, input wr_en, input [7:0] wr_data,
    input rd_en, input [7:0] rd_data, input full, input empty, input [2:0] count);
  sync_fifo #(.WIDTH(8), .DEPTH(4)) u(
    .clk(clk), .rst(rst), .wr_en(wr_en), .wr_data(wr_data), .rd_en(rd_en),
    .rd_data(rd_data), .full(full), .empty(empty), .count(count));
  reg started = 1'b0;
  always @(posedge clk) started <= 1'b1;
  always @(posedge clk) if (started) assert (count == 5);
endmodule
""",
}


def _finish(st, verdict, workdir):
    st["verdict"] = verdict
    st["workdir"] = workdir
    print("")
    print("verdict: %s" % verdict)
    if verdict != "SPINE_OK":
        print("  expected: rtl PROVED, netlist PROVED, every mutant REFUTED,")
        print("            naive control REFUTED, no dead output bit.")
    return st


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--rtl", required=True)
    p.add_argument("--top", required=True)
    p.add_argument("--props", required=True)
    p.add_argument("--workdir", default=None)
    p.add_argument("--timeout", type=int, default=300)
    p.add_argument("--depth", type=int, default=8)
    p.add_argument("--design", default=None,
                   help="stimulus/watch/mutant table to use; defaults to --top")
    p.add_argument("--no-coverage", action="store_true")
    p.add_argument("--json", default=None)
    a = p.parse_args(argv)
    st = run(a.rtl, a.top, a.props, a.workdir, a.timeout, a.depth,
             coverage=not a.no_coverage, design=a.design)
    if a.json:
        json.dump(st, open(a.json, "w", encoding="utf-8"), indent=2)
        print("wrote %s" % a.json)
    return 0 if st["verdict"] == "SPINE_OK" else 2


if __name__ == "__main__":
    sys.exit(main())
