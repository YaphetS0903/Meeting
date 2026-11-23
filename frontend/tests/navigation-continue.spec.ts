import { expect, test, type Page, type Route } from "@playwright/test";
import path from "node:path";
import { fileURLToPath } from "node:url";

const evidence = fileURLToPath(
  new URL("../../../.validation/view-status-20261029", import.meta.url),
);
const series = "series:navigation-series";
const seriesUrl = `/meetings?series=${encodeURIComponent(series)}`;
const firstId = "navigation-published";
const secondId = "navigation-next";

async function fixture(page: Page, canEdit = true) {
  const host = {
    id: "navigation-host",
    name: "测试主持人",
    department_id: "navigation-department",
    active: true,
    is_admin: canEdit,
    can_assign_tasks: canEdit,
    can_oversee_tasks: canEdit,
    has_password: true,
  };
  const draft = {
    title: "系统相关优化方案讨论周工作会议",
    kind: "department",
    department_id: host.department_id,
    series_id: "navigation-series",
    host_id: host.id,
    recorder_id: host.id,
    meeting_date: "2026-10-28",
    meeting_time: "13:30-14:30",
    location: "二楼会议室",
    attendee_ids: [host.id],
    absentees: "",
    decisions: "",
    recipient_ids: [],
    cc_ids: [],
    cc_emails: [],
    attachment_ids: [],
    items: [
      {
        id: "navigation-item",
        section: "new",
        content: "继续推进系统优化",
        category: "",
        requirements: "",
        deliverable: "",
        notes: "保留上期备注",
        owner_ids: [host.id],
        verifier_id: host.id,
        due_date: "2026-11-04",
        status: "未到期",
        task_id: "navigation-task",
        task_version: 1,
        exclusion_reason: "",
      },
    ],
  };
  const control = {
    writes: [] as string[],
    errors: [] as string[],
    unexpected: [] as string[],
    holdContinue: false,
    heldContinue: null as Route | null,
    failContinue: false,
    exports: [] as string[],
    includeDraft: false,
    hasRevision: false,
    hasRevisionChanges: false,
  };
  const record = (id: string) => {
    const published = id === firstId;
    const content = structuredClone(draft);
    if (!published) {
      content.meeting_date = "2026-11-04";
      content.items[0].section = "review";
    }
    return {
      id,
      title: content.title,
      series_id: draft.series_id,
      draft: content,
      draft_version: 1,
      published_version: published ? 2 : 0,
      has_draft: !published || control.hasRevision,
      is_revising: published && control.hasRevisionChanges,
      can_edit: canEdit,
      can_continue: published && canEdit,
      can_delete: false,
      attachments: [],
      pending_edit_requests: [],
      assessments: {},
      versions: published
        ? [
            {
              number: 1,
              snapshot: { ...content, decisions: "旧发布内容不应展示" },
            },
            { number: 2, snapshot: content },
          ]
        : [],
    };
  };
  await page.addInitScript(() => {
    if (location.origin === "http://127.0.0.1:5202")
      sessionStorage.setItem("token", "isolated-navigation-test");
  });
  page.on("pageerror", (error) => control.errors.push(error.message));
  await page.route("**/api/**", async (route) => {
    const request = route.request(),
      url = new URL(request.url()),
      p = url.pathname;
    if (request.method() !== "GET")
      control.writes.push(`${request.method()} ${p}`);
    let body: any;
    if (p === "/api/me") body = host;
    else if (p === "/api/auth/options")
      body = { wecom: false, wecom_qr: false };
    else if (p === "/api/meeting-people")
      body = {
        users: [host],
        departments: [{ id: host.department_id, name: "测试部门" }],
      };
    else if (p === "/api/reports/access")
      body = { mode: "all", departments: [], users: [host], topics: [] };
    else if (p === "/api/directory")
      body = {
        users: [host],
        departments: [{ id: host.department_id, name: "信息部" }],
        series: [{ id: draft.series_id, name: draft.title }],
      };
    else if (p === "/api/meeting-series")
      body = {
        items: [
          {
            key: series,
            name: draft.title,
            meeting_count: 1,
            latest_date: draft.meeting_date,
          },
        ],
        total: 1,
        meeting_total: 1,
      };
    else if (p === "/api/meetings")
      body = {
        items:
          url.searchParams.get("view") === "history"
            ? []
            : [
                {
                  id: firstId,
                  title: draft.title,
                  meeting_date: draft.meeting_date,
                  series_id: draft.series_id,
                  department_id: draft.department_id,
                  kind: draft.kind,
                  host_name: host.name,
                  item_count: 1,
                  published_version: 2,
                  has_draft: control.hasRevision,
                  is_revising: control.hasRevisionChanges,
                  can_continue: canEdit,
                },
                ...(control.includeDraft
                  ? [
                      {
                        id: secondId,
                        title: "下一场待发布草稿",
                        meeting_date: "2026-10-29",
                        series_id: draft.series_id,
                        department_id: draft.department_id,
                        kind: draft.kind,
                        host_name: host.name,
                        item_count: 1,
                        published_version: 0,
                        has_draft: true,
                        can_continue: false,
                      },
                    ]
                  : []),
              ],
        total:
          url.searchParams.get("view") === "history"
            ? 0
            : control.includeDraft
              ? 2
              : 1,
      };
    else if (p.startsWith(`/api/meetings/${firstId}/export/`)) {
      control.exports.push(p + url.search);
      await route.fulfill({
        body: "isolated latest export",
        contentType: "application/octet-stream",
      });
      return;
    } else if (p === `/api/meetings/${firstId}/continue`) {
      if (control.failContinue) {
        await route.fulfill({
          status: 503,
          json: { detail: "创建暂时失败，请重试" },
        });
        return;
      }
      if (control.holdContinue) {
        control.heldContinue = route;
        return;
      }
      body = record(secondId);
    } else if (
      p === `/api/meetings/${firstId}` ||
      p === `/api/meetings/${secondId}`
    ) {
      if (request.method() !== "GET") {
        await route.fulfill({
          status: 503,
          json: { detail: "模拟保存暂不可用" },
        });
        return;
      }
      body = record(p.split("/").pop()!);
    } else if (
      p === "/api/tasks" ||
      p === "/api/verifications" ||
      p === "/api/direct-assignments"
    )
      body = [];
    else if (p === "/api/direct-assignments/options")
      body = { can_assign: true, scope: "all", users: [host] };
    else if (p === "/api/task-overview/options")
      body = { scope: "all", users: [host], departments: [] };
    else if (p === "/api/task-overview")
      body = { items: [], total: 0, summary: {} };
    else if (p === "/api/reports")
      body = { summary: { counts: {} }, details: [], groups: [] };
    else if (p === "/api/history-meeting-departments")
      body = { items: [], meeting_total: 0 };
    else if (["/api/admin/config", "/api/admin/integration-checks"].includes(p))
      body = {};
    else if (p === "/api/admin/native-todo")
      body = { enabled: false, allowed_users: [] };
    else if (p === "/api/admin/operations") body = null;
    else if (p === "/api/admin/reminders")
      body = {
        enabled: false,
        hour: 9,
        before_days: 1,
        verification_hours: 24,
      };
    else if (
      [
        "/api/admin/notifications",
        "/api/admin/audit",
        "/api/admin/reassignment",
        "/api/admin/directory-sync-history",
        "/api/admin/aliases",
      ].includes(p)
    )
      body = [];
    else {
      control.unexpected.push(`${request.method()} ${p}`);
      await route.fulfill({
        status: 500,
        json: { detail: "Unexpected isolated navigation endpoint" },
      });
      return;
    }
    await route.fulfill({ json: body });
  });
  return control;
}

const back = (page: Page) =>
  page.getByRole("button", { name: "返回", exact: true });
function clean(control: Awaited<ReturnType<typeof fixture>>) {
  expect(control.errors).toEqual([]);
  expect(control.unexpected).toEqual([]);
}

test("各工作页面提供统一返回，回到原会议系列且不会循环跳转", async ({
  page,
}) => {
  const control = await fixture(page);
  await page.goto(seriesUrl);
  for (const name of [
    "我的待办",
    "直接派发任务",
    "任务总览",
    "待我验证",
    "统计报表",
    "系统管理",
  ]) {
    await page.getByRole("link", { name, exact: true }).click();
    await expect(page.locator("h1")).toHaveText(name);
    await expect(back(page)).toBeEnabled();
    await back(page).click();
    await expect(page).toHaveURL(
      (url) =>
        url.pathname === "/meetings" &&
        url.searchParams.get("series") === series,
    );
  }
  await back(page).click();
  await expect(page).toHaveURL(/\/meetings$/);
  await expect(back(page)).toBeDisabled();
  expect(control.writes).toEqual([]);
  clean(control);
});

test("直接打开会议详情也能逐级返回系列与首页，不返回外部网站", async ({
  page,
}) => {
  const control = await fixture(page);
  await page.goto("data:text/html,<p>external page</p>");
  await page.goto(`/meetings/${firstId}?series=${encodeURIComponent(series)}`);
  await expect(back(page)).toBeEnabled();
  await back(page).click();
  await expect(page).toHaveURL(
    (url) =>
      url.pathname === "/meetings" && url.searchParams.get("series") === series,
  );
  await back(page).click();
  await expect(page).toHaveURL(/\/meetings$/);
  await expect(back(page)).toBeDisabled();
  clean(control);
});

test("返回沿用会议未保存保护，取消离开保留输入，确认后才离开", async ({
  page,
}) => {
  const control = await fixture(page);
  await page.goto(seriesUrl);
  await page.getByRole("button", { name: "＋ 新建会议", exact: true }).click();
  await page.getByRole("button", { name: "＋ 新增工作", exact: true }).click();
  const content = page.getByRole("textbox", {
    name: "工作内容及要求",
    exact: true,
  });
  await content.fill("尚未保存的重要任务");
  await back(page).click();
  const dialog = page.getByRole("dialog", {
    name: "未保存的修改",
    exact: true,
  });
  await dialog.getByRole("button", { name: "取消", exact: true }).click();
  await expect(content).toHaveValue("尚未保存的重要任务");
  await expect(page).toHaveURL(/\/meetings\/new\?series=/);
  await back(page).click();
  await dialog.getByRole("button", { name: "确定", exact: true }).click();
  await expect(page).toHaveURL(
    (url) =>
      url.pathname === "/meetings" && url.searchParams.get("series") === series,
  );
  expect(control.writes).toEqual([]);
  clean(control);
});

test("详情顶部持续新建可取消、失败重试，等待期间不会重复创建并保留系列", async ({
  page,
}) => {
  const control = await fixture(page);
  await page.goto(`/meetings/${firstId}?series=${encodeURIComponent(series)}`);
  const create = page
    .locator(".page-heading")
    .getByRole("button", { name: "持续新建", exact: true });
  await expect(create).toBeVisible();
  const revise = page
    .locator(".page-heading")
    .getByRole("button", { name: "创建修订版本", exact: true });
  await expect(revise).toBeVisible();
  await expect(create).toHaveCSS("background-color", "rgb(29, 100, 200)");
  await expect(revise).toHaveCSS("background-color", "rgb(255, 240, 213)");
  await expect(
    page.getByRole("combobox", { name: "查看版本", exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByText("旧发布内容不应展示", { exact: true }),
  ).toHaveCount(0);
  for (const [fmt, label] of [
    ["xlsx", "Excel 纪要"],
    ["pdf", "PDF 纪要"],
  ]) {
    const pending = page.waitForEvent("download");
    await page.getByRole("button", { name: label, exact: true }).click();
    const download = await pending;
    expect(download.suggestedFilename()).not.toMatch(/-V\d+/);
    expect(control.exports.at(-1)).toBe(
      `/api/meetings/${firstId}/export/${fmt}?version=2`,
    );
  }
  await page.mouse.move(1, 1);
  await expect(
    page.getByRole("button", { name: "接续新建", exact: true }),
  ).toHaveCount(0);
  await page.screenshot({
    path: path.join(evidence, "detail-desktop.png"),
    fullPage: false,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(create).toBeVisible();
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth),
  ).toBeLessThanOrEqual(390);
  await page.screenshot({
    path: path.join(evidence, "detail-mobile.png"),
    fullPage: true,
  });
  await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
  await expect(back(page)).toBeInViewport();
  await page.evaluate(() => window.scrollTo(0, 0));
  await create.click();
  let dialog = page.getByRole("dialog", { name: "持续新建", exact: true });
  await dialog.getByRole("button", { name: "取消", exact: true }).click();
  expect(control.writes).toEqual([]);
  control.failContinue = true;
  await create.click();
  await dialog.getByRole("button", { name: "确认新建", exact: true }).click();
  await expect(
    page.getByText("创建暂时失败，请重试", { exact: true }),
  ).toBeVisible();
  await expect(create).toBeEnabled();
  control.failContinue = false;
  control.holdContinue = true;
  await create.click();
  await dialog.getByRole("button", { name: "确认新建", exact: true }).click();
  await expect.poll(() => !!control.heldContinue).toBe(true);
  await expect(create).toBeDisabled();
  await create.dispatchEvent("click");
  expect(control.writes).toEqual(
    Array(2).fill(`POST /api/meetings/${firstId}/continue`),
  );
  await control.heldContinue!.fulfill({ json: { id: secondId } });
  await expect(page).toHaveURL(
    (url) =>
      url.pathname === `/meetings/${secondId}` &&
      url.searchParams.get("series") === series,
  );
  await expect(
    page.getByRole("heading", { name: "在线会议编辑", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("继续推进系统优化", { exact: true }),
  ).toBeVisible();
  await expect(
    page
      .locator("tr")
      .filter({ hasText: "继续推进系统优化" })
      .locator("textarea"),
  ).toHaveValue("保留上期备注");
  clean(control);
});

test("普通参会人可返回，持续新建仍遵守原有权限", async ({ page }) => {
  const control = await fixture(page, false);
  await page.goto(`/meetings/${firstId}?series=${encodeURIComponent(series)}`);
  await expect(
    page.getByRole("heading", { name: "会议纪要", exact: true }),
  ).toBeVisible();
  await expect(back(page)).toBeEnabled();
  await expect(
    page.getByRole("button", { name: "持续新建", exact: true }),
  ).toHaveCount(0);
  await back(page).click();
  await expect(page).toHaveURL(
    (url) =>
      url.pathname === "/meetings" && url.searchParams.get("series") === series,
  );
  expect(control.writes).toEqual([]);
  clean(control);
});

for (const mobile of [false, true]) {
  test(`有新草稿时查看28号会议仍只读，不新建或保存草稿（${mobile ? "手机" : "桌面"}）`, async ({
    page,
  }) => {
    if (mobile) await page.setViewportSize({ width: 390, height: 844 });
    const control = await fixture(page);
    control.includeDraft = true;
    control.hasRevision = true;
    await page.goto(seriesUrl);
    const names = page.locator("main .table-link:visible");
    await expect(names).toHaveText([
      "系统相关优化方案讨论周工作会议",
      "下一场待发布草稿",
    ]);
    const latestRow = page
      .locator(
        mobile
          ? ".meeting-current-mobile li"
          : ".meeting-current-table .el-table__body tr",
      )
      .filter({ hasText: "系统相关优化方案讨论周工作会议" });
    await expect(latestRow.locator(".el-tag")).toHaveText("已发布");
    await expect(
      page.getByText("最新已发布会议置顶 · 草稿不影响历史归档", {
        exact: true,
      }),
    ).toHaveCount(0);
    await page.screenshot({
      path: path.join(evidence, `current-${mobile ? "mobile" : "desktop"}.png`),
      fullPage: true,
    });
    await names.first().click();
    await expect(page).toHaveURL(new RegExp(`/meetings/${firstId}\\?series=`));
    await expect(
      page.getByRole("heading", { name: "会议纪要", exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: "预览并发布", exact: true }),
    ).toHaveCount(0);
    await page.waitForTimeout(1900);
    expect(control.writes).toEqual([]);
    await back(page).click();
    await expect(latestRow.locator(".el-tag")).toHaveText("已发布");
    await latestRow.locator(".el-tag").screenshot({
      path: path.join(
        evidence,
        `published-status-${mobile ? "mobile" : "desktop"}.png`,
      ),
    });
    await page
      .getByRole("button", { name: "查看", exact: true })
      .first()
      .click();
    await expect(
      page.getByRole("heading", { name: "会议纪要", exact: true }),
    ).toBeVisible();
    expect(control.writes).toEqual([]);
    await back(page).click();
    // A real pending edit must remain visible; viewing does not hide revisions.
    control.hasRevisionChanges = true;
    await page.reload();
    await expect(latestRow.locator(".el-tag")).toHaveText("已发布 · 修订中");
    clean(control);
  });

  test(`列表接续新建必须确认，取消不创建，确认期间防止重复创建（${mobile ? "手机" : "桌面"}）`, async ({
    page,
  }) => {
    if (mobile) await page.setViewportSize({ width: 390, height: 844 });
    const control = await fixture(page);
    await page.goto(seriesUrl);
    const create = page.getByRole("button", { name: "接续新建", exact: true });
    await create.click();
    const dialog = page.getByRole("dialog", {
      name: "确认接续新建",
      exact: true,
    });
    await expect(dialog.locator(".el-message-box__message")).toHaveText(
      "是否确认新建会议？",
    );
    expect(control.writes).toEqual([]);
    await dialog.getByRole("button", { name: "取消", exact: true }).click();
    expect(control.writes).toEqual([]);
    await expect(page).toHaveURL(new RegExp("/meetings\\?series="));
    control.holdContinue = true;
    await create.click();
    await dialog.getByRole("button", { name: "确认新建", exact: true }).click();
    await expect.poll(() => !!control.heldContinue).toBe(true);
    await expect(create).toBeDisabled();
    await create.dispatchEvent("click");
    expect(control.writes).toEqual([`POST /api/meetings/${firstId}/continue`]);
    await control.heldContinue!.fulfill({ json: { id: secondId } });
    await expect(page).toHaveURL(new RegExp(`/meetings/${secondId}\\?series=`));
    await expect(
      page.getByRole("heading", { name: "在线会议编辑", exact: true }),
    ).toBeVisible();
    clean(control);
  });
}
