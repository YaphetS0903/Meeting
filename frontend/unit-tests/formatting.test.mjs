import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import vm from "node:vm";
import ts from "typescript";

function loadModule(name) {
  const code = ts.transpileModule(
    fs.readFileSync(new URL(`../src/${name}.ts`, import.meta.url), "utf8"),
    { compilerOptions: { module: ts.ModuleKind.CommonJS } },
  ).outputText;
  const context = vm.createContext({ exports: {}, Date, Intl });
  vm.runInContext(code, context);
  return context.exports;
}
const { beijingTime } = loadModule("time");
const { assignmentDeliveryMessage } = loadModule("assignmentDelivery");

test("SQLite UTC, explicit UTC and PostgreSQL offsets show the same Beijing time", () => {
  const previous = process.env.TZ;
  try {
    for (const zone of ["UTC", "Asia/Shanghai", "America/New_York"]) {
      process.env.TZ = zone;
      const expected = beijingTime("2026-10-26T01:04:05Z");
      assert.equal(beijingTime("2026-10-26T01:04:05.123456"), expected);
      assert.equal(beijingTime("2026-10-26 01:04:05"), expected);
      assert.equal(beijingTime("2026-10-26T09:04:05+08:00"), expected);
      assert.match(expected, /9:04:05/);
      assert.match(beijingTime("2026-10-25T23:30:00"), /2026\/10\/26.*7:30:00/);
    }
  } finally {
    if (previous === undefined) delete process.env.TZ;
    else process.env.TZ = previous;
  }
});
test("missing and invalid timestamps do not break the page", () => {
  assert.equal(beijingTime(null), "—");
  assert.equal(beijingTime("invalid"), "—");
});
test("notification copy separates skipped, queued, sent, failed and unknown", () => {
  assert.match(assignmentDeliveryMessage("skipped"), /未发送/);
  assert.doesNotMatch(assignmentDeliveryMessage("skipped"), /排队|已发送/);
  assert.match(assignmentDeliveryMessage("pending"), /排队/);
  assert.match(assignmentDeliveryMessage("sending"), /正在发送/);
  assert.match(assignmentDeliveryMessage("sent"), /已发送/);
  assert.match(assignmentDeliveryMessage("failed"), /失败/);
  assert.match(assignmentDeliveryMessage("blocked"), /失败/);
  assert.match(assignmentDeliveryMessage("uncertain"), /待核实/);
  assert.match(assignmentDeliveryMessage(undefined), /待核实/);
});
