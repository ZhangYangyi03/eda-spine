#!/bin/bash
# phys/mutate.sh <routed.v> <out.v> <which>
#
# The negative control for the physical stage: break one connection in the routed
# netlist and let the same gate answer. Two mutations are here because the FIRST
# one tried silently proved nothing:
#
#   mux_a    MUX2_X1 _16_ .A(cnt[0]) -> .A(cnt[1])
#            On the routed counter this is INVISIBLE to the property. The mux's
#            A input is the hold-path, selected when en=0, and the property is
#            guarded by `pen`, so it never observes a hold. A PASS here is a fact
#            about the property, not about the netlist -- which is exactly the
#            trap the RTL-level spine hit from the other side (its first control
#            also came back PROVED).
#   dff_d    DFF_X1 _26_ .D(_00_) -> .D(_02_)
#            cnt[0]'s flop now takes cnt[2]'s next-state term. The property is
#            about exactly this adjacency and goes FAIL in 3 steps.
set -eu
IN="$1"; OUT="$2"; WHICH="${3:-dff_d}"
python3 - "$IN" "$OUT" "$WHICH" <<'PY'
import sys
src, out, which = sys.argv[1], sys.argv[2], sys.argv[3]
t = open(src).read()
pairs = {
 "mux_a": ("MUX2_X1 _16_ (.A(cnt[0]),", "MUX2_X1 _16_ (.A(cnt[1]),"),
 "dff_d": ("DFF_X1 _26_ (.D(_00_),",   "DFF_X1 _26_ (.D(_02_),"),
}
old, new = pairs[which]
assert old in t, "anchor not found for %s" % which
open(out, "w").write(t.replace(old, new, 1))
print("patched (%s): %s -> %s" % (which, old.strip(), new.strip()))
PY
