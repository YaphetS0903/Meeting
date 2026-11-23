import { expect, test, type Page } from "@playwright/test";
import { fileURLToPath } from "node:url";

const evidence = fileURLToPath(
  new URL(
    "../../../.validation/postpone-date-strike-20261104/",
    import.meta.url,
  ),
);

async function fixture(
  page: Page,
  published = false,
  options: {
    pendingDate?: string;
    publishedDate?: string;
    arranged?: boolean;
  } = {},
) {
  const me = {
    id: "host",
    name: "主持人",
    active: true,
    is_admin: false,
    department_id: "dept",
  };
  const item = (id: string, status: string) => ({
    id,
    status,
    section: "review",
    content:
      id === "future" ? "新增模检具验收清单台账整理" : "宝鸡焊接机到位时间确认",
    owner_ids: ["host"],
    verifier_id: "host",
    due_date: "2026-11-13",
    task_id: `task-${id}`,
    task_version: 1,
    category: "",
    requirements: "",
    notes: "",
    deliverable: "",
  });
  let draft: any = {
    title: "采购周会",
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
    items: [item("future", "未到期"), item("unfinished", "未完成")],
  };
  if (options.pendingDate)
    draft.items[0].postponed_due_date = options.pendingDate;
  if (options.arranged)
    draft.items.push({
      ...item("future", "未到期"),
      id: "arranged-future",
      section: "new",
      due_date: "2026-11-21",
      content: "本次已安排的模检具任务",
    });
  const publishedSnapshot = structuredClone(draft);
  if (options.publishedDate)
    publishedSnapshot.items[0].postponed_due_date = options.publishedDate;
  const previouslyPublished = published || !!options.publishedDate;
  let version = 1;
  const writes: any[] = [],
    previews: any[] = [],
    errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.addInitScript(() =>
    sessionStorage.setItem("token", "isolated-postpone"),
  );
  await page.route("**/api/**", async (route) => {
    const request = route.request(),
      path = new URL(request.url()).pathname;
    let body: any;
    if (path === "/api/me") body = me;
    else if (path === "/api/auth/options") body = { wecom: false };
    else if (path === "/api/directory")
      body = {
        users: [me],
        departments: [{ id: "dept", name: "采购部" }],
        series: [{ id: "series", name: "采购周会", department_id: "dept" }],
      };
    else if (path === "/api/meeting-people") body = { users: [me] };
    else if (path === "/api/meetings/postpone/preview") {
      previews.push(structuredClone(draft));
      return route.fulfill({
        path: fileURLToPath(
          new URL("./fixtures/preview-minutes.pdf", import.meta.url),
        ),
        contentType: "application/pdf",
      });
    } else if (path === "/api/meetings/postpone") {
      if (request.method() === "PUT") {
        expect(published).toBe(false);
        expect(request.postDataJSON().expected_version).toBe(version);
        draft = request.postDataJSON().draft;
        writes.push(structuredClone(draft));
        version++;
      }
      body = {
        id: "postpone",
        ...draft,
        draft,
        draft_version: version,
        published_version: previouslyPublished ? 1 : 0,
        can_edit: true,
        has_draft: !published,
        attachments: [],
        assessments: {},
        versions: previouslyPublished
          ? [{ number: 1, snapshot: publishedSnapshot }]
          : [],
      };
    } else if (path.startsWith("/api/native-todo/")) body = { enabled: false };
    else throw new Error(`Unexpected API: ${request.method()} ${path}`);
    await route.fulfill({ json: body });
  });
  await page.goto("/meetings/postpone");
  if (options.publishedDate && !published)
    await page.getByRole("button", { name: "编辑会议", exact: true }).click();
  return { writes, previews, errors, draft: () => draft };
}

for (const width of [1366, 390]) {
  test(`cancel wrong pending date saves and removes it from preview at ${width}px`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 900 });
    const control = await fixture(page, false, { pendingDate: "2026-11-19" });
    const row = page
      .locator("tbody tr")
      .filter({ hasText: "新增模检具验收清单台账整理" });
    const cancel = row.getByRole("button", { name: "取消延期", exact: true });
    await expect(row.locator(".postponed-date")).toContainText("2026-11-19");
    await cancel.scrollIntoViewIfNeeded();
    await expect(page.locator(".el-loading-mask")).toHaveCount(0);
    await page.screenshot({
      path: `${evidence}/cancel-available-${width}.png`,
    });
    await cancel.click();
    await expect(row.locator(".postponed-date")).toHaveCount(0);
    await expect(cancel).toHaveCount(0);
    await expect(row.locator(".review-due-date")).toHaveText("2026-11-13");
    await expect(
      row.getByRole("button", { name: "延期", exact: true }),
    ).toBeEnabled();
    await page.getByRole("button", { name: "保存草稿", exact: true }).click();
    await page.reload();
    await expect(row.locator(".postponed-date")).toHaveCount(0);
    expect(control.draft().items).toHaveLength(2);
    expect(control.draft().items[0]).toMatchObject({
      due_date: "2026-11-13",
      postponed_due_date: null,
      task_version: 1,
      task_id: "task-future",
    });
    await row
      .getByRole("button", { name: "延期", exact: true })
      .scrollIntoViewIfNeeded();
    await page.screenshot({ path: `${evidence}/cancelled-${width}.png` });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    await page.getByRole("button", { name: "预览并发布", exact: true }).click();
    await expect.poll(() => control.previews.length).toBe(1);
    expect(control.previews[0].items[0].postponed_due_date).toBeNull();
    expect(control.errors).toEqual([]);
  });

  test(`postpone requires date, preserves original and preview, at ${width}px`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 900 });
    const control = await fixture(page);
    const row = page
      .locator("tbody tr")
      .filter({ hasText: "新增模检具验收清单台账整理" });
    await row.getByRole("button", { name: "延期", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "任务延期", exact: true });
    await dialog.getByRole("button", { name: "确认延期" }).click();
    await expect(
      dialog.getByText("请选择延期日期", { exact: true }),
    ).toBeVisible();
    await dialog.getByRole("button", { name: "取消", exact: true }).click();
    expect(control.draft().items).toHaveLength(2);
    expect(control.draft().items[0].postponed_due_date).toBeUndefined();
    await row.getByRole("button", { name: "延期", exact: true }).click();
    await dialog
      .getByRole("combobox", { name: "延期日期", exact: true })
      .fill("2026-11-20");
    await dialog
      .getByRole("combobox", { name: "延期日期", exact: true })
      .press("Tab");
    await dialog.getByRole("button", { name: "确认延期" }).click();
    await expect(dialog).toBeHidden();
    await expect(row.locator(".review-due-date")).toContainText("2026-11-13");
    await expect(row.locator(".postponed-date")).toHaveText("2026-11-20");
    await expect(row.locator(".original-due-date")).toHaveCSS("text-decoration-line", "line-through");
    await expect(row.locator(".postponed-date")).toHaveCSS("border-top-style", "none");
    await expect(row.locator(".postponed-date span")).toHaveCSS("font-weight", "400");
    await page.getByRole("button", { name: "保存草稿", exact: true }).click();
    expect(control.draft().items).toHaveLength(2);
    expect(control.draft().items[0]).toMatchObject({
      due_date: "2026-11-13",
      postponed_due_date: "2026-11-20",
      task_version: 1,
    });
    await page.reload();
    await expect(row.locator(".postponed-date")).toContainText("2026-11-20");
    await row.evaluate((node) => node.scrollIntoView({ block: "center" }));
    await row.locator(".review-due-date").scrollIntoViewIfNeeded();
    await expect(page.locator(".el-loading-mask")).toHaveCount(0);
    await page.screenshot({ path: `${evidence}/postpone-${width}.png` });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    await page.getByRole("button", { name: "预览并发布", exact: true }).click();
    await expect.poll(() => control.previews.length).toBe(1);
    expect(control.previews[0].items[0].postponed_due_date).toBe("2026-11-20");
    expect(control.errors).toEqual([]);
  });
}

test("an existing current arrangement explains disabled postponement and cancellation preserves it", async ({
  page,
}) => {
  const control = await fixture(page, false, {
    arranged: true,
    pendingDate: "2026-11-19",
  });
  const row = page
    .locator("tbody tr")
    .filter({ hasText: "新增模检具验收清单台账整理" });
  await expect(
    row.getByRole("button", { name: "延期", exact: true }),
  ).toBeDisabled();
  await row
    .getByLabel("已在本次任务单中安排，请在下方该任务修改日期", { exact: true })
    .focus();
  await expect(page.getByRole("tooltip")).toContainText(
    "已在本次任务单中安排，请在下方该任务修改日期",
  );
  await row.getByRole("button", { name: "取消延期", exact: true }).click();
  await page.getByRole("button", { name: "保存草稿", exact: true }).click();
  expect(control.draft().items).toHaveLength(3);
  expect(control.draft().items[2]).toMatchObject({
    section: "new",
    due_date: "2026-11-21",
  });
  expect(control.draft().items[0].postponed_due_date).toBeNull();
  await expect(
    row.getByRole("button", { name: "延期", exact: true }),
  ).toBeDisabled();
  expect(control.errors).toEqual([]);
});

test("cancelling a revised pending date restores the published date", async ({
  page,
}) => {
  const control = await fixture(page, false, {
    pendingDate: "2026-11-25",
    publishedDate: "2026-11-20",
  });
  const row = page
    .locator("tbody tr")
    .filter({ hasText: "新增模检具验收清单台账整理" });
  await row.getByRole("button", { name: "取消延期", exact: true }).click();
  await expect(row.locator(".postponed-date")).toContainText("2026-11-20");
  await expect(
    row.getByRole("button", { name: "取消延期", exact: true }),
  ).toHaveCount(0);
  await page.getByRole("button", { name: "保存草稿", exact: true }).click();
  await page.reload();
  await page.getByRole("button", { name: "编辑会议", exact: true }).click();
  await expect(row.locator(".postponed-date")).toContainText("2026-11-20");
  expect(control.draft().items[0].postponed_due_date).toBe("2026-11-20");
  expect(control.errors).toEqual([]);
});

test("unfinished extension still creates a current arrangement with required date", async ({
  page,
}) => {
  const control = await fixture(page);
  const row = page
    .locator("tbody tr")
    .filter({ hasText: "宝鸡焊接机到位时间确认" });
  await expect(
    row.getByRole("button", { name: "延期", exact: true }),
  ).toHaveCount(0);
  await row.getByRole("button", { name: "延续安排", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "延续安排", exact: true });
  await dialog.getByRole("button", { name: "确认延续" }).click();
  await expect(
    dialog.getByText("请选择延续日期", { exact: true }),
  ).toBeVisible();
  await dialog
    .getByRole("combobox", { name: "延续日期", exact: true })
    .fill("2026-11-21");
  await dialog
    .getByRole("combobox", { name: "延续日期", exact: true })
    .press("Tab");
  await dialog.getByRole("button", { name: "确认延续" }).click();
  await page.getByRole("button", { name: "保存草稿", exact: true }).click();
  expect(control.draft().items).toHaveLength(3);
  expect(control.draft().items[2]).toMatchObject({
    section: "new",
    due_date: "2026-11-21",
    postponed_due_date: null,
  });
  await expect(
    row.getByRole("button", { name: "已延续", exact: true }),
  ).toBeDisabled();
  expect(control.errors).toEqual([]);
});

test("read-only minutes offer no postponement or draft write", async ({
  page,
}) => {
  const control = await fixture(page, true, { pendingDate: "2026-11-19" });
  await expect(
    page.getByRole("heading", { name: "上期会议任务跟踪情况", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "延期", exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "取消延期", exact: true }),
  ).toHaveCount(0);
  expect(control.writes).toEqual([]);
  expect(control.errors).toEqual([]);
});
