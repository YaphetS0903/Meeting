import { test, expect } from "@playwright/test";
import { orderReviewByOwners } from "../src/reviewSorting";

const row = (id: string, owner_ids: string[], origin = "review") => ({
  id,
  owner_ids,
  source: { continuation_origin: origin },
  content: `工作${id}`,
  status: "未到期",
  notes: `备注${id}`,
  due_date: "2026-11-20",
});
const ids = (rows: { id: string }[]) => rows.map((r) => r.id);

test("历史责任人已集中：保留全部任务顺序，包括中间的共同责任人", () => {
  const original = [
    row("a1", ["a"]),
    row("a2", ["a"]),
    row("ab", ["b", "a"]),
    row("a3", ["a"]),
    row("a4", ["a"]),
    row("b1", ["b"]),
    row("b2", ["b"]),
  ];
  original[0].status = "未完成";
  original[1].status = "已完成";
  const before = structuredClone(original),
    result = orderReviewByOwners(original);
  expect(result).toEqual(before);
  expect(result.every((r, i) => r === original[i])).toBe(true);
  expect(original).toEqual(before);
});

test("责任人分散时正常归组，组内保持原顺序，不按姓名或完成结果重排", () => {
  const original = [
    row("b1", ["b"]),
    row("a1", ["a"]),
    row("b2", ["b"]),
    row("a2", ["a"]),
  ];
  const before = structuredClone(original),
    sorted = orderReviewByOwners(original);
  expect(ids(sorted)).toEqual(["b1", "b2", "a1", "a2"]);
  expect(original).toEqual(before);
  expect(ids(orderReviewByOwners(sorted))).toEqual(ids(sorted));
});

test("共同负责的任务不会把不同人的历史分组合并成一大组", () => {
  const original = [
    row("a", ["a"]),
    row("ab", ["a", "b"]),
    row("b", ["b"]),
    row("bc", ["b", "c"]),
    row("c", ["c"]),
  ];
  expect(orderReviewByOwners(original)).toEqual(original);
  const disordered = [
    row("a1", ["a"]),
    row("bc", ["b", "c"]),
    row("a2", ["a"]),
  ];
  expect(ids(orderReviewByOwners(disordered))).toEqual(["a1", "a2", "bc"]);
});

test("只有多人组合时忽略责任人ID顺序，每项只出现一次", () => {
  const original = [
    row("ab1", ["a", "b"]),
    row("c", ["c"]),
    row("ab2", ["b", "a"]),
  ];
  const result = orderReviewByOwners(original);
  expect(ids(result)).toEqual(["ab1", "ab2", "c"]);
  expect(new Set(ids(result)).size).toBe(original.length);
});

test("保留历史人员分组和组内顺序，延续和新工作紧随对应的历史分组", () => {
  const original = [
    row("new-b", ["b"], "new"),
    row("old-a1", ["a"]),
    row("old-ab", ["b", "a"]),
    row("old-a2", ["a"]),
    row("old-b", ["b"]),
    row("extension-a", ["a"], "extension"),
    row("new-a", ["a"], "new"),
    row("extension-b", ["b"], "extension"),
  ];
  const expected = [
    "old-a1",
    "old-ab",
    "old-a2",
    "extension-a",
    "new-a",
    "old-b",
    "extension-b",
    "new-b",
  ];
  const result = orderReviewByOwners(original);
  expect(ids(result)).toEqual(expected);
  expect(ids(orderReviewByOwners(result))).toEqual(expected);
});

test("散乱历史归组后新增的共同责任事项一次归位，重复点击不再改变", () => {
  const original = [
    row("old-a1", ["a"]),
    row("old-b", ["b"]),
    row("old-a2", ["a"]),
    row("extension-ab", ["a", "b"], "extension"),
    row("new-a", ["a"], "new"),
  ];
  const result = orderReviewByOwners(original);
  expect(ids(result)).toEqual([
    "old-a1",
    "old-a2",
    "extension-ab",
    "new-a",
    "old-b",
  ]);
  expect(ids(orderReviewByOwners(result))).toEqual(ids(result));
});

test("来源映射优先，缺少来源的历史导入仍保留组内原顺序", () => {
  const original = [
    row("new-a", ["a"], "new"),
    row("old-a1", ["a"], "new"),
    row("old-a2", ["a"], "new"),
    row("old-b", ["b"], "new"),
  ];
  expect(
    ids(
      orderReviewByOwners(original, {
        "old-a1": "review",
        "old-a2": "review",
        "old-b": "review",
      }),
    ),
  ).toEqual(["old-a1", "old-a2", "new-a", "old-b"]);
  const imported = original.map((r) => ({ ...r, source: {} }));
  expect(orderReviewByOwners(imported)).toEqual(imported);
});

test("空任务、未匹配责任人都不丢失，未匹配事项互相独立", () => {
  expect(orderReviewByOwners([])).toEqual([]);
  const original = [row("blank1", []), row("a", ["a"]), row("blank2", [])];
  expect(orderReviewByOwners(original)).toEqual(original);
});
