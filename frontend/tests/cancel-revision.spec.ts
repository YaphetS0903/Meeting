import { test, expect, type Page, type Route } from "@playwright/test";
import path from "node:path";
import { fileURLToPath } from "node:url";

const evidence = fileURLToPath(new URL("../../../.validation/cancel-revision-20261106/shots/", import.meta.url));
async function fixture(
  page: Page,
  options: { canEdit?: boolean; published?: boolean; hasDraft?: boolean } = {},
) {
  const actor = {
    id: "revision-host",
    name: "测试主持人",
    active: true,
    is_admin: true,
    department_id: "revision-dep",
  };
  const original = {
    title: "系统相关优化方案讨论周工作会议",
    department_id: actor.department_id,
    series_id: null,
    host_id: actor.id,
    recorder_id: actor.id,
    kind: "department",
    meeting_date: "2026-11-04",
    meeting_time: "14:00",
    location: "二楼会议室",
    attendee_ids: [actor.id],
    reviewer_id: null,
    absentees: "",
    decisions: "",
    recipient_ids: [],
    cc_ids: [],
    cc_emails: [],
    attachment_ids: [],
    items: [
      {
        id: "revision-item",
        section: "new",
        content: "已发布的任务内容",
        owner_ids: [actor.id],
        due_date: "2026-11-12",
        status: "未到期",
        notes: "",
        verifier_id: actor.id,
        task_id: "revision-task",
        task_version: 1,
      },
    ],
  };
  const state = {
    draft: structuredClone(original),
    draft_version: 6,
    published_version: options.published === false ? 0 : 1,
    has_draft: options.hasDraft !== false,
  };
  state.draft.items[0].content = "未发布的修订内容";
  const controls = {
    saves: [] as any[],
    cancels: [] as any[],
    writes: [] as string[],
    errors: [] as string[],
    failCancel: 0,
    failRefresh: false,
    holdSave: false,
    heldSave: null as Route | null,
    state,
  };
  const view = () => ({
    id: "revision-meeting",
    ...state,
    title: original.title,
    can_edit: options.canEdit !== false,
    can_continue: !!state.published_version,
    can_delete: false,
    is_revising: state.has_draft,
    versions: state.published_version
      ? [{ number: 1, snapshot: original }]
      : [],
    attachments: [],
    notifications: [],
    assessments: {},
  });
  await page.addInitScript(() =>
    sessionStorage.setItem("token", "isolated-revision-test"),
  );
  page.on("pageerror", (e) => controls.errors.push(e.message));
  await page.route("**/api/**", async (route) => {
    const req = route.request(),
      p = new URL(req.url()).pathname;
    if (req.method() !== "GET") controls.writes.push(`${req.method()} ${p}`);
    let body: any;
    if (p === "/api/me") body = actor;
    else if (p === "/api/auth/options") body = { wecom: false };
    else if (p === "/api/directory" || p === "/api/meeting-people")
      body = {
        users: [actor],
        departments: [{ id: actor.department_id, name: "信息部" }],
        series: [],
      };
    else if (p === "/api/meetings/revision-meeting/revision/cancel") {
      controls.cancels.push(req.postDataJSON());
      if (controls.failCancel) {
        await route.fulfill({
          status: controls.failCancel,
          json: { detail: "会议已被其他人修改，请刷新核对后再取消修订" },
        });
        return;
      }
      state.draft = structuredClone(original);
      state.has_draft = false;
      state.draft_version++;
      body = view();
    } else if (p === "/api/meetings/revision-meeting") {
      if (req.method() === "PUT") {
        controls.saves.push(req.postDataJSON());
        state.draft = req.postDataJSON().draft;
        state.draft_version++;
        if (controls.holdSave) {
          controls.heldSave = route;
          return;
        }
      } else if (controls.failRefresh && !state.has_draft) {
        await route.fulfill({ status: 503, json: { detail: "详情暂不可用" } });
        return;
      }
      body = view();
    } else throw new Error(`Unexpected request ${req.method()} ${p}`);
    await route.fulfill({ json: body });
  });
  await page.goto("/meetings/revision-meeting");
  await expect(page.locator("h1")).toBeVisible();
  return { controls, view };
}

for (const width of [1366, 390]) {
  test(`取消修订 ${width}px 恢复已发布内容且没有自动保存或通知`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 844 });
    const { controls } = await fixture(page);
    await page.getByRole("button", { name: "编辑会议", exact: true }).click();
    await page
      .getByRole("textbox", { name: "工作内容及要求", exact: true })
      .fill("尚未保存的修改");
    await page.getByRole("button", { name: "取消修订", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "取消修订", exact: true });
    await expect(dialog).toContainText("放弃本次所有未发布的修改");
    await page.screenshot({
      path: path.join(evidence, `confirmation-${width}.png`),
    });
    // Leave the confirmation open beyond the autosave debounce.
    await page.waitForTimeout(1800);
    expect(controls.saves).toHaveLength(0);
    await dialog
      .getByRole("button", { name: "确认取消修订", exact: true })
      .click();
    await expect(
      page.getByRole("button", { name: "创建修订版本", exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: "取消修订", exact: true }),
    ).toHaveCount(0);
    await expect(page.locator("body")).toContainText("已发布的任务内容");
    await expect(page.locator(".el-loading-mask:visible")).toHaveCount(0);
    await page.screenshot({
      path: path.join(evidence, `restored-${width}.png`),
    });
    await page.waitForTimeout(1800);
    expect(controls.cancels).toEqual([{ expected_version: 6 }]);
    expect(controls.saves).toHaveLength(0);
    expect(controls.writes).toEqual([
      "POST /api/meetings/revision-meeting/revision/cancel",
    ]);
    expect(controls.errors).toEqual([]);
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth),
    ).toBeLessThanOrEqual(width);
  });
}

test("取消修订选择继续修订保留输入并恢复自动保存", async ({ page }) => {
  const { controls } = await fixture(page);
  await page.getByRole("button", { name: "编辑会议", exact: true }).click();
  const input = page.getByRole("textbox", {
    name: "工作内容及要求",
    exact: true,
  });
  await input.fill("需要保留的输入");
  await page.getByRole("button", { name: "取消修订", exact: true }).click();
  await page.getByRole("button", { name: "继续修订", exact: true }).click();
  await expect(input).toHaveValue("需要保留的输入");
  await expect.poll(() => controls.saves.length).toBe(1);
  expect(controls.cancels).toHaveLength(0);
});

test("取消修订等待在途保存并使用最新版本", async ({ page }) => {
  const { controls, view } = await fixture(page);
  controls.holdSave = true;
  await page.getByRole("button", { name: "编辑会议", exact: true }).click();
  await page
    .getByRole("textbox", { name: "工作内容及要求", exact: true })
    .fill("正在保存的输入");
  await expect.poll(() => !!controls.heldSave).toBe(true);
  await page.getByRole("button", { name: "取消修订", exact: true }).click();
  await expect(
    page.getByRole("dialog", { name: "取消修订", exact: true }),
  ).toHaveCount(0);
  await controls.heldSave!.fulfill({ json: view() });
  await page.getByRole("button", { name: "确认取消修订", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "创建修订版本", exact: true }),
  ).toBeVisible();
  expect(controls.cancels).toEqual([{ expected_version: 7 }]);
  expect(controls.saves).toHaveLength(1);
});

for (const status of [409, 503]) {
  test(`取消修订失败 ${status} 保留本地输入且不继续自动覆盖`, async ({
    page,
  }) => {
    const { controls } = await fixture(page);
    controls.failCancel = status;
    await page.getByRole("button", { name: "编辑会议", exact: true }).click();
    const input = page.getByRole("textbox", {
      name: "工作内容及要求",
      exact: true,
    });
    await input.fill("失败后保留输入");
    await page.getByRole("button", { name: "取消修订", exact: true }).click();
    await page
      .getByRole("button", { name: "确认取消修订", exact: true })
      .click();
    await expect(
      page.getByRole("alert").filter({ hasText: "会议已被其他人修改" }).first(),
    ).toBeVisible();
    await expect(input).toHaveValue("失败后保留输入");
    await page.waitForTimeout(1800);
    expect(controls.state.has_draft).toBe(true);
    expect(controls.saves).toHaveLength(0);
  });
}

test("取消修订成功后详情刷新失败仍保持已发布状态", async ({ page }) => {
  const { controls } = await fixture(page);
  controls.failRefresh = true;
  await page.getByRole("button", { name: "取消修订", exact: true }).click();
  await page.getByRole("button", { name: "确认取消修订", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "创建修订版本", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "取消修订", exact: true }),
  ).toHaveCount(0);
  expect(controls.cancels).toHaveLength(1);
});

for (const options of [
  { canEdit: false },
  { published: false },
  { hasDraft: false },
]) {
  test(`取消修订入口限制 ${JSON.stringify(options)}`, async ({ page }) => {
    await fixture(page, options);
    await expect(
      page.getByRole("button", { name: "取消修订", exact: true }),
    ).toHaveCount(0);
  });
}
