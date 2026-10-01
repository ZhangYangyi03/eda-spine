// Property for the FIFO case: occupancy is bounded and full/empty agree with it.
//
// Instantiated with NO parameter override, on purpose. The same file has to be
// read twice: once beside the RTL (where sync_fifo has parameters) and once
// beside the gate netlist (where it does not -- Yosys emits a flat module and
// `#(.DEPTH(4))` is then "Can't find object for defparam", measured). The
// design's own defaults are the contract in both cases.
module sync_fifo_check(
    input clk, input rst, input wr_en, input [7:0] wr_data,
    input rd_en, input [7:0] rd_data, input full, input empty, input [2:0] count);
  localparam DEPTH = 4;
  sync_fifo u(
    .clk(clk), .rst(rst), .wr_en(wr_en), .wr_data(wr_data), .rd_en(rd_en),
    .rd_data(rd_data), .full(full), .empty(empty), .count(count));
  reg started = 1'b0;
  always @(posedge clk) started <= 1'b1;
  always @(posedge clk) if (!started) assume (rst);   // reset at t=0
  always @(posedge clk) if (started) begin
    assert (count <= DEPTH);
    assert (full == (count == DEPTH));
    assert (empty == (count == 0));
  end
endmodule
