import { expect, test, type Page } from "@playwright/test";

const evidence = "../../.validation/task-history-20261125";
const user = {
  id: "history-owner",
  name: "历史责任人",
  active: true,
  is_admin: false,
  can_assign_tasks: false,
  can_oversee_tasks: false,
};
const base = {
  owner_ids: [user.id],
  owner_names: [user.name],
  verifier_id: "history-verifier",
  verifier_name: "验证同事",
  due_date: "2026-10-20",
  source_meeting_id: null,
  source_meeting_title: "直接派发",
  requests: [],
  versions: [],
  requirements: "核对原始材料并保留验收记录。",
  deliverable: "任务完成记录",
  can_edit: true,
  can_transfer: true,
  can_withdraw: true,
  can_view_source: false,
  version: 1,
};
const current = {
  ...base,
  id: "current-work",
  content: "当前待办中的真实任务",
  status: "未完成",
};
const records = [
  {
    ...base,
    id: "closed-work",
    content: "去年已关闭的归档任务",
    todo_status: "关闭",
    closed_at: "2026-10-21T01:00:00Z",
    source_meeting_id: "online-meeting",
    inbox_meeting_title: "年度归档工作会议",
    inbox_meeting_date: "2026-10-10",
    can_view_source: true,
  },
  {
    ...base,
    id: "old-unfinished",
    content: "以前未完成的检查任务",
    todo_status: "未完成",
  },
  {
    ...base,
    id: "old-excluded",
    content: "以前不统计的工作事项",
    todo_status: "不统计",
  },
  {
    ...base,
    id: "history:meeting:imported",
    content: "历史纪要导入的整改工作",
    todo_status: "未到期",
    history_only: true,
    notes: "当期会议备注：按原定计划继续推进。",
    source_meeting_id: "historical-meeting",
    inbox_meeting_title: "八月份部门例会",
    inbox_meeting_date: "2026-08-15",
    can_view_source: true,
  },
  {
    ...base,
    id: "old-withdrawn",
    content: "已撤回的历史任务",
    todo_status: "已撤回",
    withdrawn_at: "2026-10-19T01:00:00Z",
  },
  {
    ...base,
    id: "old-done",
    content: "以前已完成的盘点任务",
    todo_status: "已完成",
  },
];

async function setup(
  page: Page,
  options: { many?: boolean; url?: string } = {},
) {
  const control = {
    historyQueries: [] as URLSearchParams[],
    currentQueries: [] as URLSearchParams[],
    details: [] as string[],
    writes: [] as string[],
    unexpected: [] as string[],
    errors: [] as string[],
    failed: false,
    hold: false,
    release: () => {},
  };
  const rows = options.many
    ? [
        ...records,
        ...Array.from({ length: 19 }, (_, index) => ({
          ...base,
          id: `older-${index}`,
          content: `更早的历史任务 ${index}`,
          todo_status: "未完成",
        })),
      ]
    : records;
  page.on("pageerror", (error) => control.errors.push(error.message));
  await page.addInitScript(() =>
    sessionStorage.setItem("token", "local-history-test-token"),
  );
  await page.route("**/api/**", async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    let body: unknown;
    let status = 200;
    if (req.method() !== "GET") {
      control.writes.push(`${req.method()} ${url.pathname}`);
      status = 400;
      body = { detail: "禁止写入隔离历史验证" };
    } else if (url.pathname === "/api/me") body = user;
    else if (url.pathname === "/api/auth/options") body = { wecom: false };
    else if (url.pathname === "/api/directory")
      body = {
        users: [
          user,
          { ...user, id: base.verifier_id, name: base.verifier_name },
        ],
        departments: [],
        series: [],
      };
    else if (url.pathname === "/api/tasks") {
      control.currentQueries.push(url.searchParams);
      body = { items: [current], total: 1 };
    } else if (url.pathname === "/api/task-history") {
      control.historyQueries.push(url.searchParams);
      if (control.hold)
        await new Promise<void>((resolve) => {
          control.release = resolve;
        });
      if (control.failed) {
        status = 503;
        body = { detail: "隔离历史服务暂时不可用" };
      } else {
        const matching = rows.filter(
          (row) =>
            (!url.searchParams.get("state") ||
              row.todo_status === url.searchParams.get("state")) &&
            (!url.searchParams.get("q") ||
              row.content.includes(url.searchParams.get("q")!)),
        );
        const start = Number(url.searchParams.get("offset") || 0);
        body = {
          items: matching.slice(
            start,
            start + Number(url.searchParams.get("limit") || 20),
          ),
          total: matching.length,
        };
      }
    } else {
      const id = url.pathname.match(/^\/api\/tasks\/([^/]+)$/)?.[1];
      const row = [current, ...rows].find((row) => row.id === id);
      if (row) {
        control.details.push(id!);
        body = {
          ...row,
          inbox_meeting_title: undefined,
          inbox_meeting_date: undefined,
          owner_names: undefined,
          verifier_name: undefined,
        };
      } else {
        control.unexpected.push(url.pathname);
        status = 404;
        body = { detail: "未预期的请求" };
      }
    }
    await route.fulfill({
      status,
      contentType: "application/json",
      body: JSON.stringify(body),
    });
  });
  await page.goto(options.url || "/tasks");
  await expect(
    page.getByRole("heading", { name: "我的待办", exact: true }),
  ).toBeVisible();
  return control;
}

const historyTab = (page: Page) =>
  page
    .getByRole("radiogroup", { name: "任务查看范围" })
    .getByText("历史任务单", { exact: true });
const currentTab = (page: Page) =>
  page
    .getByRole("radiogroup", { name: "任务查看范围" })
    .getByText("当前待办", { exact: true });
async function statusFilter(page: Page, name: string) {
  await page.locator(".filter-row .el-select__wrapper").click();
  await page.getByRole("option", { name, exact: true }).click();
}
function readOnly(control: Awaited<ReturnType<typeof setup>>) {
  expect(control.writes).toEqual([]);
  expect(control.unexpected).toEqual([]);
  expect(control.errors).toEqual([]);
}

test("历史模块包含旧任务及已关闭状态，详情只读，返回当前待办", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1366, height: 900 });
  const control = await setup(page);
  await expect(page.getByRole("table")).toContainText(current.content);
  await historyTab(page).click();
  await expect(page).toHaveURL(/view=history/);
  await expect(
    page.getByRole("radio", { name: "历史任务单", exact: true }),
  ).toBeChecked();
  await expect(page.getByRole("table")).toContainText(records[0].content);
  await expect(page.getByRole("table").locator("tbody tr")).toHaveCount(6);
  expect(control.historyQueries[0].get("mine")).toBe("true");
  expect(control.historyQueries[0].has("current")).toBe(false);
  await page.screenshot({
    path: `${evidence}/history-desktop.png`,
    animations: "disabled",
  });
  await page
    .getByRole("button", { name: records[0].content, exact: true })
    .click();
  const drawer = page.getByRole("dialog", {
    name: "历史任务单详情",
    exact: true,
  });
  await expect(drawer).toContainText("已关闭");
  await expect(drawer).toContainText("年度归档工作会议");
  await expect(drawer).toContainText("2026-10-10");
  await expect(
    drawer.getByRole("button", { name: "提交完成申请" }),
  ).toHaveCount(0);
  await expect(
    drawer.getByRole("button", { name: "调整期限 / 责任人" }),
  ).toHaveCount(0);
  await expect(drawer.getByRole("button", { name: "移交派发人" })).toHaveCount(
    0,
  );
  await expect(drawer.getByRole("button", { name: "撤回任务" })).toHaveCount(0);
  await drawer.getByRole("button", { name: "返回上一页" }).click();
  await currentTab(page).click();
  await expect(
    page.getByRole("radio", { name: "当前待办", exact: true }),
  ).toBeChecked();
  await expect(page.getByRole("table")).toContainText(current.content);
  await expect(page.getByRole("table")).not.toContainText(records[0].content);
  readOnly(control);
});

test("历史状态可分别筛选未完成、不统计、已关闭和已撤回", async ({ page }) => {
  const control = await setup(page, { url: "/tasks?view=history" });
  for (const [label, value] of [
    ["未完成", "未完成"],
    ["不统计", "不统计"],
    ["已关闭", "关闭"],
    ["已撤回", "已撤回"],
  ]) {
    await statusFilter(page, label);
    await expect
      .poll(() => control.historyQueries.at(-1)?.get("state"))
      .toBe(value);
    await expect(page.getByRole("table").locator("tbody tr")).toHaveCount(1);
    await expect(page.getByRole("table").locator("tbody tr")).toContainText(
      label,
    );
  }
  readOnly(control);
});

test("纯历史导入项可打开会议备注，无需创建或访问实时任务", async ({ page }) => {
  const control = await setup(page, { url: "/tasks?view=history" });
  await page
    .getByRole("button", { name: records[3].content, exact: true })
    .click();
  const drawer = page.getByRole("dialog", {
    name: "历史任务单详情",
    exact: true,
  });
  await expect(drawer).toContainText(records[3].notes!);
  await expect(drawer).toContainText(user.name);
  await expect(
    drawer.getByRole("link", { name: "查看来源会议 →" }),
  ).toHaveAttribute("href", "/meetings/historical-meeting");
  expect(control.details).toEqual([]);
  await expect(
    drawer.getByRole("button", { name: "提交完成申请" }),
  ).toHaveCount(0);
  readOnly(control);
});

test("历史列表分页、查询和本人筛选，切换后清除历史条件", async ({ page }) => {
  const control = await setup(page, { many: true, url: "/tasks?view=history" });
  await expect(page.getByText("共 25 条任务", { exact: true })).toBeVisible();
  await page.locator(".el-pagination .btn-next").click();
  await expect
    .poll(() => control.historyQueries.at(-1)?.get("offset"))
    .toBe("20");
  await page
    .getByRole("textbox", { name: "搜索任务", exact: true })
    .fill("整改工作");
  await page.getByRole("button", { name: "查询", exact: true }).click();
  await expect
    .poll(() => control.historyQueries.at(-1)?.get("offset"))
    .toBe("0");
  await expect(page.getByRole("table").locator("tbody tr")).toHaveCount(1);
  await page.locator(".filter-row .el-switch__core").click();
  await expect
    .poll(() => control.historyQueries.at(-1)?.get("mine"))
    .toBe("false");
  await currentTab(page).click();
  await expect.poll(() => control.currentQueries.at(-1)?.get("q")).toBeNull();
  expect(control.currentQueries.at(-1)?.get("current")).toBe("true");
  readOnly(control);
});

test("延迟的历史响应不能覆盖已经切回的当前待办", async ({ page }) => {
  const control = await setup(page);
  control.hold = true;
  await historyTab(page).click();
  await expect.poll(() => control.historyQueries.length).toBe(1);
  await currentTab(page).click();
  await expect(page.getByRole("table")).toContainText(current.content);
  control.release();
  await expect(page.getByRole("table")).not.toContainText(records[0].content);
  readOnly(control);
});

test("历史加载失败可重试，手机入口、列表和详情无横向溢出", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const control = await setup(page);
  control.failed = true;
  await historyTab(page).click();
  const alert = page
    .getByRole("alert")
    .filter({ hasText: "历史任务单加载失败" });
  await expect(alert).toContainText("隔离历史服务暂时不可用");
  control.failed = false;
  await alert.getByRole("button", { name: "重试加载" }).click();
  await expect(alert).toHaveCount(0);
  await expect(page.locator(".el-message")).toHaveCount(0, { timeout: 9000 });
  await expect(page.locator(".task-mobile-list")).toContainText(
    records[0].content,
  );
  await page.screenshot({
    path: `${evidence}/history-mobile.png`,
    animations: "disabled",
  });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await page
    .locator(".task-mobile-list")
    .getByRole("button", { name: records[3].content, exact: true })
    .click();
  await expect(
    page.getByRole("dialog", { name: "历史任务单详情" }),
  ).toContainText(records[3].notes!);
  await expect(
    page.getByRole("dialog", { name: "历史任务单详情" }),
  ).toBeVisible();
  await page.screenshot({
    path: `${evidence}/history-mobile-detail.png`,
    animations: "disabled",
  });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  readOnly(control);
});

test("键盘可以进入历史任务单并切回当前待办", async ({ page }) => {
  const control = await setup(page);
  const currentRadio = page.getByRole("radio", {
    name: "当前待办",
    exact: true,
  });
  const historyRadio = page.getByRole("radio", {
    name: "历史任务单",
    exact: true,
  });
  await currentRadio.focus();
  await currentRadio.press("ArrowRight");
  await expect(historyRadio).toBeChecked();
  await expect(page.getByRole("table")).toContainText(records[0].content);
  await historyRadio.press("ArrowLeft");
  await expect(currentRadio).toBeChecked();
  await expect(page.getByRole("table")).toContainText(current.content);
  readOnly(control);
});
