// Property for the FSM case: the invariant is the ORDER, not the state values.
// RED may only be followed by RED-and-YELLOW; a transition that skips it is the
// classic FSM bug, and no assertion over a single state value can see it.
module traffic_fsm_check(input clk, input rst, input tick, input [1:0] state);
  localparam RED = 2'd0, RED_YELLOW = 2'd1, GREEN = 2'd2, YELLOW = 2'd3;
  traffic_fsm u(.clk(clk), .rst(rst), .tick(tick), .state(state));
  reg [1:0] pstate; reg ptick; reg prst; reg started = 1'b0;
  always @(posedge clk) begin pstate <= state; ptick <= tick; prst <= rst; started <= 1'b1; end
  always @(posedge clk) if (!started) assume (rst);   // reset at t=0
  always @(posedge clk) if (started && ptick && !prst) begin
    assert (!(pstate == RED)        || state == RED_YELLOW);
    assert (!(pstate == RED_YELLOW) || state == GREEN);
    assert (!(pstate == GREEN)      || state == YELLOW);
    assert (!(pstate == YELLOW)     || state == RED);
  end
endmodule
