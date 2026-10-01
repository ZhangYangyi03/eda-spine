// Property used by the spine's positive/negative controls.
// "the counter advances by exactly one" -- the property a skip is invisible to,
// and the one synthesis could plausibly break by collapsing the adder.
module counter_check(input clk, input rst, input en, input [3:0] cnt);
  counter u(.clk(clk), .rst(rst), .en(en), .cnt(cnt));
  reg [3:0] pcnt; reg pen; reg prst; reg started = 1'b0;
  always @(posedge clk) begin pcnt <= cnt; pen <= en; prst <= rst; started <= 1'b1; end
  always @(posedge clk) if (!started) assume (rst);   // reset at t=0
  always @(posedge clk) if (started && pen && !prst) assert (cnt == pcnt + 1'b1);
endmodule
