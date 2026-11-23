import { test, expect, type Page, type Route } from "@playwright/test";
import path from "node:path";
import { fileURLToPath } from "node:url";
const evidence = fileURLToPath(
  new URL("../../../.validation/meeting-columns-20261029/", import.meta.url),
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
    if (location.hostname === "127.0.0.1")
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
        departments: [
          { id: "company-root", name: "天瑞汽车内饰" },
          { id: "company", name: "天瑞公司", parent_id: "company-root" },
          { id: "support", name: "支持中心", parent_id: "company" },
          { id: host.department_id, name: "信息部", parent_id: "support" },
        ],
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
            ? [
                {
                  id: "history-layout",
                  title: "上期系统相关优化方案讨论周工作会议",
                  meeting_date: "2026-10-21",
                  series_id: draft.series_id,
                  department_id: draft.department_id,
                  kind: draft.kind,
                  host_name: host.name,
                  item_count: 16,
                  rate: 0.75,
                  published_version: 1,
                  has_draft: false,
                  is_revising: false,
                  can_continue: canEdit,
                  can_delete: canEdit,
                },
              ]
            : [
                {
                  id: firstId,
                  title: draft.title,
                  meeting_date: draft.meeting_date,
                  series_id: draft.series_id,
                  department_id: draft.department_id,
                  kind: draft.kind,
                  host_name: host.name,
                  item_count: 25,
                  rate: 0.857,
                  published_version: 2,
                  has_draft: control.hasRevision,
                  is_revising: control.hasRevisionChanges,
                  can_continue: canEdit,
                  can_delete: canEdit,
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
            ? 1
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

test("baseline responsive clipping evidence", async ({ page }) => {
  test.skip(process.env.RESPONSIVE_BASELINE !== "1");
  await fixture(page);
  for (const width of [1440, 1280, 1024]) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto(seriesUrl);
    const action = page
      .getByRole("button", { name: "查看", exact: true })
      .first();
    await expect(action).toBeVisible();
    console.log(
      JSON.stringify({
        width,
        action: await action.boundingBox(),
        scroll: await page.evaluate(() => ({
          viewport: innerWidth,
          document: document.documentElement.scrollWidth,
        })),
      }),
    );
    await page.screenshot({
      path: path.join(evidence, `before-${width}.png`),
      fullPage: true,
    });
  }
});

const visibleView = (page: Page) =>
  page
    .locator("button:visible")
    .filter({ hasText: /^查看$/ })
    .first();
async function withinViewport(
  page: Page,
  locator: ReturnType<Page["locator"]>,
) {
  const box = await locator.boundingBox();
  expect(box).not.toBeNull();
  expect(box!.x).toBeGreaterThanOrEqual(0);
  expect(box!.x + box!.width).toBeLessThanOrEqual(
    (await page.evaluate(() => innerWidth)) + 1,
  );
  await expect
    .poll(() =>
      locator.evaluate((el) => {
        const rect = el.getBoundingClientRect();
        const top = document.elementFromPoint(
          rect.x + rect.width / 2,
          rect.y + rect.height / 2,
        );
        return !!top && (el === top || el.contains(top));
      }),
    )
    .toBe(true);
}
for (const width of [
  1920, 1600, 1440, 1366, 1280, 1024, 820, 768, 640, 390, 320,
]) {
  test(`meeting operations remain usable at ${width}px`, async ({ page }) => {
    const control = await fixture(page);
    await page.setViewportSize({ width, height: 1000 });
    await page.goto(seriesUrl);
    await expect(visibleView(page)).toBeVisible();
    await visibleView(page).scrollIntoViewIfNeeded();
    await withinViewport(page, visibleView(page));
    const create = page
      .locator("button:visible")
      .filter({ hasText: /^接续新建$/ })
      .first();
    await withinViewport(page, create);
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth),
    ).toBeLessThanOrEqual(width);
    if (width >= 768) {
      const table = page.locator(".meeting-current-table");
      expect(await table.evaluate((el) => el.scrollWidth)).toBeLessThanOrEqual(
        await table.evaluate((el) => el.clientWidth),
      );
      await expect(table.locator("thead th .cell")).toHaveText([
        "会议日期",
        "会议名称",
        "所属部门",
        "会议类型",
        "主持人",
        "工作事项",
        "达成率",
        "状态",
        "操作",
      ]);
      const row = table.locator(".el-table__body tr").first();
      await expect(row.locator("td").nth(1)).not.toContainText("主持人");
      await expect(row.locator("td").nth(1)).not.toContainText("项工作");
      await expect(row.locator("td").nth(2)).toHaveText("信息部");
      await expect(
        row.locator("td").nth(2).locator("span[title]"),
      ).toHaveAttribute("title", "天瑞汽车内饰 / 天瑞公司 / 支持中心 / 信息部");
      await expect(row.locator("td").nth(5)).toHaveText("25");
      await expect(row.locator("td").nth(6)).toHaveText("85.7%");
      const actionButtons = row.locator(".meeting-row-actions button");
      const ys = await actionButtons.evaluateAll((buttons) =>
        buttons.map((button) => Math.round(button.getBoundingClientRect().y)),
      );
      expect(new Set(ys).size).toBe(1);
      if (width >= 1280) {
        expect(
          await table
            .locator(".el-scrollbar__wrap")
            .evaluate((el) => el.scrollWidth - el.clientWidth),
        ).toBeLessThanOrEqual(1);
      }
      const scroll = page.locator(".meeting-current-table .el-scrollbar__wrap");
      await scroll.evaluate((el) => {
        el.scrollLeft = el.scrollWidth;
      });
      await withinViewport(page, visibleView(page));
      await scroll.evaluate((el) => {
        el.scrollLeft = 0;
      });
    } else {
      await expect(page.locator(".meeting-current-mobile")).toContainText(
        "信息部",
      );
      await expect(page.locator(".meeting-current-mobile")).toContainText(
        "主持人",
      );
      await expect(page.locator(".meeting-current-mobile")).toContainText(
        "达成率",
      );
    }
    await page.screenshot({
      path: path.join(evidence, `series-${width}.png`),
      fullPage: true,
    });
    const deleteButton = page
      .locator("button:visible")
      .filter({ hasText: /^删除$/ })
      .first();
    await withinViewport(page, deleteButton);
    await page.getByRole("button", { name: "历史会议", exact: true }).click();
    const history = page.locator(
      width < 768 ? ".meeting-history-mobile" : ".meeting-history-table",
    );
    await expect(history).toContainText("上期系统相关优化方案讨论周工作会议");
    if (width >= 768)
      await expect(history.locator("thead th .cell")).toHaveText([
        "会议日期",
        "会议名称",
        "所属部门",
        "会议类型",
        "主持人",
        "工作事项",
        "达成率",
        "状态",
        "操作",
      ]);
    await history
      .getByRole("button", { name: "接续新建", exact: true })
      .scrollIntoViewIfNeeded();
    await withinViewport(
      page,
      history.getByRole("button", { name: "接续新建", exact: true }),
    );
    await withinViewport(
      page,
      history.getByRole("button", { name: "删除", exact: true }),
    );
    await page.getByRole("button", { name: "历史会议", exact: true }).click();
    await visibleView(page).click();
    await expect(
      page.getByRole("heading", { name: "会议纪要", exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: "持续新建", exact: true }),
    ).toBeVisible();
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth),
    ).toBeLessThanOrEqual(width);
    await page.getByRole("button", { name: "返回", exact: true }).click();
    await expect(visibleView(page)).toBeVisible();
    await create.click();
    const dialog = page.getByRole("dialog", {
      name: "确认接续新建",
      exact: true,
    });
    await expect(dialog).toBeVisible();
    await dialog.getByRole("button", { name: "取消", exact: true }).click();
    await page.getByRole("button", { name: "会议系列", exact: true }).click();
    await expect(page).toHaveURL(/\/meetings$/);
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth),
    ).toBeLessThanOrEqual(width);
    await page.screenshot({
      path: path.join(evidence, `overview-${width}.png`),
      fullPage: true,
    });
    expect(control.writes).toEqual([]);
    expect(control.errors).toEqual([]);
    expect(control.unexpected).toEqual([]);
  });
}

test("responsive resizing preserves filters and sidebar toggle", async ({
  page,
}) => {
  const control = await fixture(page);
  await page.goto(seriesUrl);
  await page
    .getByRole("textbox", { name: "搜索会议", exact: true })
    .fill("系统优化");
  for (const width of [1440, 1024, 390, 1280]) {
    await page.setViewportSize({ width, height: 1000 });
    await expect(page.getByRole("button", { name: /筛选会议/ })).toHaveCount(0);
    await expect(
      page.getByRole("textbox", { name: "搜索会议", exact: true }),
    ).toHaveValue("系统优化");
    const toggle = page.locator(".navigation-trigger");
    if (width < 768) {
      await toggle.click();
      await expect(page.locator(".navigation-drawer")).toBeVisible();
      await page.keyboard.press("Escape");
      await expect(page.locator(".navigation-drawer")).toBeHidden();
      await expect(page.locator(".workspace")).toHaveCSS("margin-left", "0px");
    } else {
      const before = await toggle.getAttribute("aria-expanded");
      await toggle.click();
      await expect(toggle).toHaveAttribute(
        "aria-expanded",
        before === "true" ? "false" : "true",
      );
      await expect(page.locator(".sidebar")).toBeVisible();
      await toggle.click();
      await expect(toggle).toHaveAttribute("aria-expanded", before!);
    }
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth),
    ).toBeLessThanOrEqual(width);
  }
  expect(control.writes).toEqual([]);
});

for (const width of [1280, 820, 390, 320]) {
  test(`shared pages fit ${width}px without document overflow`, async ({
    page,
  }) => {
    const control = await fixture(page);
    await page.setViewportSize({ width, height: 1000 });
    for (const [url, heading] of [
      ["/meetings", "会议纪要与任务单"],
      ["/tasks", "我的待办"],
      ["/assignments", "直接派发任务"],
      ["/task-overview", "任务总览"],
      ["/verifications", "待我验证"],
      ["/reports", "统计报表"],
      ["/settings", "系统管理"],
    ]) {
      await page.goto(url);
      await expect(page.locator("h1")).toHaveText(heading);
      await expect
        .poll(() => page.evaluate(() => document.documentElement.scrollWidth))
        .toBeLessThanOrEqual(width);
      await expect(
        page.getByRole("button", { name: "返回", exact: true }),
      ).toBeVisible();
    }
    expect(control.writes).toEqual([]);
    expect(control.errors).toEqual([]);
    expect(control.unexpected).toEqual([]);
  });
}
