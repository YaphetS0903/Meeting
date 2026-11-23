import { expect, test, type Page } from "@playwright/test";
import { fileURLToPath } from "node:url";

const evidence = fileURLToPath(
  new URL("../../../.validation/review-task-drag-20261123/", import.meta.url),
);
const item = (id: string, status: string, section = "review") => ({
  id,
  section,
  status,
  content: `事项${id}`,
  requirements: "",
  category: "",
  owner_ids: ["host"],
  verifier_id: "host",
  due_date: "2026-11-20",
  notes: `备注${id}`,
  deliverable: `输出${id}`,
  task_id: `task-${id}`,
  task_version: 2,
});
const expected = [
  "done-a",
  "done-b",
  "unfinished",
  "future",
  "excluded",
  "closed",
  "legacy-closed",
  "blank",
];

async function fixture(
  page: Page,
  published = false,
  empty = false,
  options: {
    rows?: any[];
    origins?: Record<string, string>;
    permissions?: Record<string, boolean>;
  } = {},
) {
  const me = {
    id: "host",
    name: "主持人",
    active: true,
    is_admin: false,
    department_id: "dept",
  };
  let draft: any = {
    title: "上期任务排序验收",
    department_id: "dept",
    series_id: "series",
    kind: "department",
    host_id: "host",
    recorder_id: "host",
    meeting_date: "2026-11-04",
    attendee_ids: [],
    attachment_ids: [],
    recipient_ids: [],
    cc_ids: [],
    cc_emails: [],
    items:
      options.rows ||
      (empty
        ? []
        : [
            item("closed", "关闭"),
            item("new-a", "未到期", "new"),
            item("future", "未到期"),
            item("blank", ""),
            item("done-a", "已完成"),
            item("unfinished", "未完成"),
            item("new-b", "未到期", "new"),
            item("excluded", "不统计"),
            item("done-b", "已完成"),
            item("legacy-closed", "已关闭"),
          ]),
  };
  const initial = structuredClone(draft),
    writes: any[] = [],
    previews: any[] = [],
    errors: string[] = [];
  let version = 1;
  page.on("pageerror", (error) => errors.push(error.message));
  await page.addInitScript(() =>
    sessionStorage.setItem("token", "isolated-review-sort"),
  );
  await page.route("**/api/**", async (route) => {
    const request = route.request(),
      path = new URL(request.url()).pathname;
    let body: any;
    if (path === "/api/me") body = me;
    else if (path === "/api/auth/options") body = { wecom: false };
    else if (path === "/api/directory")
      body = {
        users: [me, { ...me, id: "other", name: "共同责任人" }],
        departments: [{ id: "dept", name: "信息部" }],
        series: [{ id: "series", name: "周会", department_id: "dept" }],
      };
    else if (path === "/api/meeting-people")
      body = { users: [me, { ...me, id: "other", name: "共同责任人" }] };
    else if (path === "/api/meetings/review-sort/preview") {
      previews.push(structuredClone(draft));
      return route.fulfill({
        path: fileURLToPath(
          new URL("./fixtures/preview-minutes.pdf", import.meta.url),
        ),
        contentType: "application/pdf",
      });
    } else if (path === "/api/meetings/review-sort") {
      if (request.method() === "PUT") {
        expect(published).toBe(false);
        expect(request.postDataJSON().expected_version).toBe(version);
        draft = request.postDataJSON().draft;
        writes.push(structuredClone(draft));
        version++;
      } else expect(request.method()).toBe("GET");
      body = {
        id: "review-sort",
        ...draft,
        draft,
        draft_version: version,
        published_version: published ? 1 : 0,
        can_edit: true,
        permissions: options.permissions,
        has_draft: !published,
        attachments: [],
        assessments: {},
        continuation_origins: options.origins || {},
        versions: published ? [{ number: 1, snapshot: draft }] : [],
      };
    } else if (path.startsWith("/api/native-todo/")) body = { enabled: false };
    else throw new Error(`Unexpected API request: ${request.method()} ${path}`);
    await route.fulfill({ json: body });
  });
  await page.goto("/meetings/review-sort");
  return { initial, writes, previews, errors, draft: () => draft };
}

for (const width of [1366, 390]) {
  test(`one click stably sorts all previous tasks and saves the preview order at ${width}px`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 900 });
    const control = await fixture(page);
    const section = page.locator("section").filter({
      has: page.getByRole("heading", {
        name: "上期会议任务跟踪情况",
        exact: true,
      }),
    });
    const contents = () =>
      section.locator("tbody tr .content-cell").allTextContents();
    const button = section.getByRole("button", {
      name: "一键排序",
      exact: true,
    });
    await expect(button).toBeEnabled();
    await button.focus();
    await button.press("Enter");
    await page.getByText("按完成结果排序", { exact: true }).click();
    await page.getByRole("button", { name: "确认排序", exact: true }).click();
    await expect.poll(contents).toEqual(expected.map((id) => `事项${id}`));
    await page.getByRole("button", { name: "保存草稿", exact: true }).click();
    await expect.poll(() => control.writes.length).toBeGreaterThan(0);
    const saved = structuredClone(control.draft());
    expect(
      saved.items
        .filter((row: any) => row.section === "review")
        .map((row: any) => row.id),
    ).toEqual(expected);
    expect(saved.items.filter((row: any) => row.section === "new")).toEqual(
      control.initial.items.filter((row: any) => row.section === "new"),
    );
    expect(saved.items).toHaveLength(control.initial.items.length);
    for (const original of control.initial.items)
      expect(saved.items.find((row: any) => row.id === original.id)).toEqual(
        original,
      );
    await page.reload();
    await expect.poll(contents).toEqual(expected.map((id) => `事项${id}`));
    await button.scrollIntoViewIfNeeded();
    await expect(page.locator(".el-loading-mask")).toHaveCount(0);
    await page.screenshot({
      path: `${evidence}/review-sort-${width}.png`,
      fullPage: false,
    });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    // Repeating an already applied sort does not dirty or resave the draft.
    const writeCount = control.writes.length;
    await button.click();
    await page.getByText("按完成结果排序", { exact: true }).click();
    await page.getByRole("button", { name: "确认排序", exact: true }).click();
    await page.getByRole("button", { name: "保存草稿", exact: true }).click();
    expect(control.writes).toHaveLength(writeCount);
    // Complete the required blank result, then sort again using its latest value.
    const blankRow = section
      .locator("tbody tr")
      .filter({ hasText: "事项blank" });
    await blankRow.evaluate((node) => node.scrollIntoView({ block: "center" }));
    await blankRow.locator(".el-select__wrapper").click();
    await page.getByRole("option", { name: "已完成", exact: true }).click();
    await button.click();
    await page.getByText("按完成结果排序", { exact: true }).click();
    await page.getByRole("button", { name: "确认排序", exact: true }).click();
    const finalOrder = ["done-a", "done-b", "blank", ...expected.slice(2, -1)];
    await expect.poll(contents).toEqual(finalOrder.map((id) => `事项${id}`));
    await page.getByRole("button", { name: "预览并发布", exact: true }).click();
    await expect.poll(() => control.previews.length).toBe(1);
    expect(
      control.previews[0].items
        .filter((row: any) => row.section === "review")
        .map((row: any) => row.id),
    ).toEqual(finalOrder);
    expect(control.errors).toEqual([]);
  });
}

for (const width of [1366, 390]) {
  test(`previous-task dragging preserves row data through saving, reopening and preview at ${width}px`, async ({
    page,
  }, testInfo) => {
    await page.setViewportSize({ width, height: 900 });
    const control = await fixture(page, false, false, {
      rows: [
        item("a", "已完成"),
        item("new-a", "未到期", "new"),
        {
          ...item("b", "未完成"),
          owner_ids: ["other"],
          source: { date_history: ["2026-10-31"] },
        },
        item("c", "未到期"),
        item("new-b", "未到期", "new"),
      ],
    });
    const table = page.locator(".meeting-review-table");
    const rows = table.locator("tbody > tr[data-review-id]");
    const ids = () =>
      rows.evaluateAll((nodes) =>
        nodes.map((node) => (node as HTMLElement).dataset.reviewId),
      );
    const handle = (id: string) =>
      table.locator(`[data-review-id="${id}"] .task-drag-handle`);
    await expect(table.locator(".task-drag-handle")).toHaveCount(3);
    await expect(page.locator(".el-loading-mask")).toHaveCount(0);
    await table
      .locator('[data-review-id="b"] td[data-label="备注"] textarea')
      .fill("排序前修改的备注b");
    await handle("c").scrollIntoViewIfNeeded();
    // Start below the sticky actions, then keep the mouse held while scrolling
    // on narrow screens where review cards are taller than the viewport.
    await handle("c").evaluate((node) =>
      window.scrollBy({
        top: node.getBoundingClientRect().top - 650,
        behavior: "instant",
      }),
    );
    const destination = table.locator('[data-review-id="a"] td').first();
    if (width >= 768) {
      await handle("c").dragTo(destination, {
        targetPosition: { x: 4, y: 4 },
      });
    } else {
      const from = (await handle("c").boundingBox())!;
      await page.mouse.move(from.x + from.width / 2, from.y + from.height / 2);
      await page.mouse.down();
      await page.mouse.move(
        from.x + from.width / 2,
        from.y + from.height / 2 - 12,
        { steps: 3 },
      );
      await expect(table.locator('[data-review-id="c"]')).toHaveClass(
        /task-order-chosen/,
      );
      await destination.evaluate((node) =>
        window.scrollBy({
          top: node.getBoundingClientRect().top - 400,
          behavior: "instant",
        }),
      );
      const to = (await destination.boundingBox())!;
      await page.mouse.move(to.x + 4, to.y + 4, { steps: 10 });
      await page.mouse.up();
    }
    await expect.poll(ids).toEqual(["c", "a", "b"]);
    await handle("c").press("ArrowUp");
    await expect.poll(ids).toEqual(["c", "a", "b"]);
    await handle("c").press("ArrowDown");
    await expect.poll(ids).toEqual(["a", "c", "b"]);
    await handle("b").press("ArrowDown");
    await expect.poll(ids).toEqual(["a", "c", "b"]);
    // Form inputs must still belong to their own task after both sorts.
    await table
      .locator('[data-review-id="c"] td[data-label="备注"] textarea')
      .fill("排序后修改的备注c");
    const assertRows = (draft: any) => {
      expect(
        draft.items
          .filter((row: any) => row.section === "review")
          .map((row: any) => row.id),
      ).toEqual(["a", "c", "b"]);
      expect(draft.items.filter((row: any) => row.section === "new")).toEqual(
        control.initial.items.filter((row: any) => row.section === "new"),
      );
      expect(draft.items).toHaveLength(control.initial.items.length);
      for (const original of control.initial.items) {
        expect(draft.items.find((row: any) => row.id === original.id)).toEqual({
          ...original,
          ...(original.id === "b"
            ? { notes: "排序前修改的备注b" }
            : original.id === "c"
              ? { notes: "排序后修改的备注c" }
              : {}),
        });
      }
    };
    await page.getByRole("button", { name: "保存草稿", exact: true }).click();
    assertRows(control.draft());
    await page.reload();
    await expect.poll(ids).toEqual(["a", "c", "b"]);
    await expect(table.locator('[data-review-id="b"] textarea')).toHaveValue(
      "排序前修改的备注b",
    );
    await expect(table.locator('[data-review-id="c"] textarea')).toHaveValue(
      "排序后修改的备注c",
    );
    await table.locator("xpath=ancestor::section[1]").evaluate((node) =>
      window.scrollBy({
        top: node.getBoundingClientRect().top - 170,
        behavior: "instant",
      }),
    );
    await expect(page.locator(".el-loading-mask")).toHaveCount(0);
    await page.screenshot({
      path: testInfo.outputPath(`review-drag-${width}.png`),
    });
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth),
    ).toBeLessThanOrEqual(width);
    await page.getByRole("button", { name: "预览并发布", exact: true }).click();
    await expect.poll(() => control.previews.length).toBeGreaterThan(0);
    assertRows(control.previews.at(-1));
    await page.getByRole("button", { name: "返回编辑", exact: true }).click();
    await page.getByRole("button", { name: "一键排序", exact: true }).click();
    await page.getByText("按完成结果排序", { exact: true }).click();
    await page.getByRole("button", { name: "确认排序", exact: true }).click();
    await expect.poll(ids).toEqual(["a", "b", "c"]);
    expect(control.errors).toEqual([]);
  });
}

test("editing without assessment permission can reorder without changing results", async ({
  page,
}) => {
  const control = await fixture(page, false, false, {
    rows: [item("a", "已完成"), item("b", "未完成")],
    permissions: { edit: true, assess: false },
  });
  const table = page.locator(".meeting-review-table");
  await expect(table.getByRole("combobox").first()).toBeDisabled();
  await table
    .locator('[data-review-id="b"] .task-drag-handle')
    .press("ArrowUp");
  await page.getByRole("button", { name: "保存草稿", exact: true }).click();
  expect(control.draft().items).toEqual([...control.initial.items].reverse());
  expect(control.errors).toEqual([]);
});

test.describe("previous-task touch dragging", () => {
  test.use({
    hasTouch: true,
    isMobile: true,
    viewport: { width: 390, height: 900 },
  });
  test("holding a previous-task handle reorders linked tasks and preserves editable notes", async ({
    page,
  }) => {
    const control = await fixture(page, false, false, {
      rows: [item("a", "已完成"), item("b", "未完成"), item("c", "未到期")],
    });
    const table = page.locator(".meeting-review-table");
    const handle = table.locator('[data-review-id="c"] .task-drag-handle');
    await expect(handle).toBeVisible();
    await expect(page.locator(".el-loading-mask")).toHaveCount(0);
    await handle.scrollIntoViewIfNeeded();
    const from = (await handle.boundingBox())!;
    const x = from.x + from.width / 2,
      startY = from.y + from.height / 2;
    const client = await page.context().newCDPSession(page);
    await client.send("Input.dispatchTouchEvent", {
      type: "touchStart",
      touchPoints: [{ x, y: startY, id: 1 }],
    });
    await expect(table.locator('[data-review-id="c"]')).toHaveClass(
      /task-order-chosen/,
    );
    await client.send("Input.dispatchTouchEvent", {
      type: "touchMove",
      touchPoints: [{ x, y: startY - 12, id: 1 }],
    });
    const destination = table.locator('[data-review-id="a"] td').first();
    await destination.evaluate((node) =>
      window.scrollBy({
        top: node.getBoundingClientRect().top - 400,
        behavior: "instant",
      }),
    );
    const to = (await destination.boundingBox())!;
    for (let step = 1; step <= 12; step++) {
      await client.send("Input.dispatchTouchEvent", {
        type: "touchMove",
        touchPoints: [
          { x, y: startY + ((to.y + 4 - startY) * step) / 12, id: 1 },
        ],
      });
    }
    const ids = () =>
      table
        .locator("tbody > tr[data-review-id]:not(.sortable-fallback)")
        .evaluateAll((nodes) =>
          nodes.map((node) => (node as HTMLElement).dataset.reviewId),
        );
    await expect.poll(ids).toEqual(["c", "a", "b"]);
    await client.send("Input.dispatchTouchEvent", {
      type: "touchEnd",
      touchPoints: [],
    });
    await client.detach();
    await expect(table.locator(".sortable-fallback")).toHaveCount(0);
    await table.locator('[data-review-id="c"] textarea').fill("触屏排序后备注");
    await page.getByRole("button", { name: "保存草稿", exact: true }).click();
    expect(control.draft().items).toEqual([
      { ...control.initial.items[2], notes: "触屏排序后备注" },
      control.initial.items[0],
      control.initial.items[1],
    ]);
    expect(control.errors).toEqual([]);
  });
});

test("published read-only viewing offers no sort and writes no draft", async ({
  page,
}) => {
  const control = await fixture(page, true);
  await expect(
    page.getByRole("heading", { name: "上期会议任务跟踪情况", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "一键排序", exact: true }),
  ).toHaveCount(0);
  await expect(
    page.locator(".meeting-review-table .task-drag-handle"),
  ).toHaveCount(0);
  expect(control.writes).toEqual([]);
  expect(control.errors).toEqual([]);
});

test("empty previous-task list disables sorting", async ({ page }) => {
  const control = await fixture(page, false, true);
  await expect(
    page.getByRole("button", { name: "一键排序", exact: true }),
  ).toBeDisabled();
  expect(control.writes).toEqual([]);
  expect(control.errors).toEqual([]);
});

for (const width of [1366, 390]) {
  test(`historical owner blocks preserve their order and interleave additions once at ${width}px`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 900 });
    const make = (id: string, owners: string[], origin: string) => ({
      ...item(id, id.startsWith("old") ? "未到期" : ""),
      owner_ids: owners,
      postponed_due_date: id === "old-pair" ? "2026-12-21" : null,
      source: width === 1366 ? { continuation_origin: origin } : {},
    });
    const rows = [
      make("fresh-pair", ["host", "other"], "new"),
      make("old-single", ["host"], "review"),
      make("extension-pair", ["other", "host"], "extension"),
      make("fresh-single", ["host"], "new"),
      make("old-pair", ["other", "host"], "review"),
      make("extension-single", ["host"], "extension"),
      item("untouched-new", "未到期", "new"),
    ];
    const origins = Object.fromEntries(
      rows
        .filter((r) => r.section === "review")
        .map((r) => [
          r.id,
          r.id.startsWith("old")
            ? "review"
            : r.id.startsWith("extension")
              ? "extension"
              : "new",
        ]),
    );
    const control = await fixture(page, false, false, {
      rows,
      origins: width === 390 ? origins : {},
    });
    const section = page.locator("section").filter({
      has: page.getByRole("heading", {
        name: "上期会议任务跟踪情况",
        exact: true,
      }),
    });
    const contents = () =>
      section.locator("tbody tr .content-cell").allTextContents();
    const before = rows
      .filter((r) => r.section === "review")
      .map((r) => `事项${r.id}`);
    const open = () =>
      section.getByRole("button", { name: "一键排序", exact: true }).click();
    await open();
    await expect(
      page.getByRole("dialog", { name: "一键排序", exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole("radio", { name: "按责任人分类", exact: true }),
    ).toBeChecked();
    await page.getByRole("button", { name: "取消", exact: true }).click();
    expect(await contents()).toEqual(before);
    await open();
    await page
      .getByRole("dialog", { name: "一键排序", exact: true })
      .screenshot({
        path: `${evidence}/owner-sort-dialog-${width}.png`,
      });
    await page.getByRole("button", { name: "确认排序", exact: true }).click();
    const expectedIds = [
      "old-single",
      "old-pair",
      "extension-pair",
      "extension-single",
      "fresh-pair",
      "fresh-single",
    ];
    await expect.poll(contents).toEqual(expectedIds.map((id) => `事项${id}`));
    await page.getByRole("button", { name: "保存草稿", exact: true }).click();
    await expect.poll(() => control.writes.length).toBeGreaterThan(0);
    const saved = control.draft();
    expect(saved.items).toHaveLength(rows.length);
    for (const original of rows)
      expect(saved.items.find((row: any) => row.id === original.id)).toEqual(
        original,
      );
    await page.reload();
    await expect.poll(contents).toEqual(expectedIds.map((id) => `事项${id}`));
    await open();
    await page.getByRole("button", { name: "确认排序", exact: true }).click();
    await expect.poll(contents).toEqual(expectedIds.map((id) => `事项${id}`));
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    expect(control.errors).toEqual([]);
  });
}

for (const width of [1366, 390]) {
  test(`already grouped imported history keeps the shared task in its original position at ${width}px`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 900 });
    const rows = [
      { ...item("a1", "未到期"), owner_ids: ["host"] },
      { ...item("a2", "未到期"), owner_ids: ["host"] },
      { ...item("shared", "未到期"), owner_ids: ["other", "host"] },
      { ...item("a3", "未到期"), owner_ids: ["host"] },
      { ...item("a4", "未完成"), owner_ids: ["host"] },
      { ...item("b1", "未到期"), owner_ids: ["other"] },
      { ...item("b2", "未到期"), owner_ids: ["other"] },
    ];
    const control = await fixture(page, false, false, { rows });
    const section = page.locator("section").filter({
      has: page.getByRole("heading", {
        name: "上期会议任务跟踪情况",
        exact: true,
      }),
    });
    const contents = () =>
      section.locator("tbody tr .content-cell").allTextContents();
    const expected = rows.map((r) => r.content);
    await section
      .getByRole("button", { name: "一键排序", exact: true })
      .click();
    await expect(page.getByRole("dialog")).toContainText(
      "已集中排列的历史事项保留原顺序",
    );
    await page.getByRole("button", { name: "确认排序", exact: true }).click();
    expect(await contents()).toEqual(expected);
    await page.getByRole("button", { name: "保存草稿", exact: true }).click();
    // Already grouped history is unchanged, so saving need not write a new draft.
    expect(control.writes).toHaveLength(0);
    expect(control.draft().items).toEqual(rows);
    await page.reload();
    await expect.poll(contents).toEqual(expected);
    await page.screenshot({
      path: `${evidence}/preserved-history-${width}.png`,
      fullPage: true,
      animations: "disabled",
    });
    expect(control.errors).toEqual([]);
  });
}
