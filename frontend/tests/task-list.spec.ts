import { expect, test, type Page } from "@playwright/test";
import path from "node:path";

const screenshotDir = "../../.validation/inbox-due-status-20261104";
const names = ["袁可炜", "李晓明", "陈思远", "王静", "张宁", "赵海燕"];
const longContent =
  "完成生产设备巡检方案更新，逐项核实各部门整改措施、验收标准和附件资料，并汇总所有责任人的执行结果；" +
  "长链接与编号 ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 也应保持在任务内容单元格内。";
const tasks = [
  {
    id: "isolated-task-1",
    code: "RW-20261026-001",
    content: "完成十月份会议纪要归档与任务核对",
    category: "部门例会 · 工作安排",
    created_at: "2026-10-25T18:10:00Z",
    start_date: "2026-10-26",
    source_meeting_id: "isolated-source-meeting",
    source_meeting_title: "2026年十月份部门工作例会",
    inbox_meeting_title: "2026年十月份部门工作例会",
    inbox_meeting_date: "2026-10-21",
    todo_status: "到期未验证",
    can_view_source: true,
    due_date: "2026-10-30",
    status: "未到期",
    owner_ids: ["isolated-person-0"],
    owner_names: [names[0]],
    verifier_id: "isolated-verifier",
    verifier_name: "周经理",
  },
  {
    id: "isolated-task-2",
    code: "RW-20261026-002",
    content: longContent,
    issuer_name: "部门负责人",
    created_at: "2026-10-19T08:30:00Z",
    start_date: "2026-10-19",
    source_meeting_id: null,
    source_meeting_title: "直接派发",
    can_view_source: false,
    due_date: "2026-10-25",
    status: "未完成",
    owner_ids: names.map((_, index) => `isolated-person-${index}`),
    owner_names: names,
    verifier_id: "isolated-verifier",
    verifier_name: "周经理",
    latest_request: { state: "pending" },
  },
  {
    id: "isolated-task-3",
    code: "RW-20261026-003",
    content: "核验仓库月度盘点结果",
    category: "仓储工作",
    created_at: "2026-10-18T03:40:00Z",
    start_date: "2026-10-18",
    source_meeting_id: "isolated-source-meeting-long",
    inbox_meeting_date: "2026-10-28",
    source_meeting_title:
      "仓储物流与生产管理联合专项会议：四季度库存盘点、设备巡检和部门协同整改工作部署",
    can_view_source: true,
    due_date: "2026-10-24",
    status: "已完成",
    owner_ids: ["isolated-person-2"],
    owner_names: [names[2]],
    verifier_id: "isolated-verifier",
    verifier_name: "周经理",
  },
  {
    id: "isolated-task-4",
    code: "RW-20261026-004",
    content: "汇总下一季度的培训需求",
    issuer_name: "行政负责人",
    created_at: "2026-10-24T09:30:00Z",
    start_date: "2026-10-24",
    source_meeting_id: null,
    source_meeting_title: "直接派发",
    can_view_source: false,
    due_date: null,
    status: "未到期",
    owner_ids: ["isolated-person-1"],
    owner_names: [names[1]],
    verifier_id: "isolated-verifier",
    verifier_name: "周经理",
  },
  {
    id: "isolated-task-5",
    code: "RW-20261026-005",
    content: "会议来源受限的工作安排",
    created_at: null,
    start_date: null,
    source_meeting_id: "isolated-hidden-source-meeting",
    source_meeting_title: null,
    can_view_source: false,
    due_date: "2026-10-28",
    status: "未到期",
    todo_status: "到期未验证",
    owner_ids: ["isolated-person-3"],
    owner_names: [names[3]],
    verifier_id: "isolated-verifier",
    verifier_name: "周经理",
  },
  {
    id: "isolated-future",
    code: "RW-FUTURE",
    content: "电子用印培训和试运行",
    source_meeting_id: "isolated-source-meeting",
    source_meeting_title: "2026年十月份部门工作例会",
    inbox_meeting_title: "2026年十月份部门工作例会",
    inbox_meeting_date: "2026-10-21",
    due_date: "2026-11-16",
    status: "未到期",
    todo_status: "未到期验证",
    owner_ids: ["isolated-person-0"],
    owner_names: [names[0]],
    verifier_id: "isolated-verifier",
    verifier_name: "周经理",
    can_view_source: true,
  },
  {
    id: "isolated-closed",
    code: "RW-CLOSED",
    content: "已关闭任务不再展示",
    status: "关闭",
    closed_at: "2026-10-26T04:00:00Z",
    owner_names: [],
    owner_ids: [],
  },
];
const visibleTasks = tasks.filter((t) => t.status !== "关闭");

async function mockTasks(
  page: Page,
  options: { empty?: boolean; failed?: boolean } = {},
) {
  const controls = {
    failed: !!options.failed,
    queries: [] as URLSearchParams[],
    details: [] as string[],
    writes: [] as string[],
    unexpected: [] as string[],
    pageErrors: [] as string[],
  };
  const user = {
    id: "isolated-person-0",
    name: names[0],
    active: true,
    is_admin: false,
    can_assign_tasks: false,
    can_oversee_tasks: false,
  };
  page.on("pageerror", (error) => controls.pageErrors.push(error.message));
  await page.addInitScript(() =>
    sessionStorage.setItem("token", "isolated-task-list-token"),
  );
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const pathname = url.pathname;
    let body: any;
    let status = 200;
    if (request.method() !== "GET") {
      controls.writes.push(`${request.method()} ${pathname}`);
      status = 400;
      body = { detail: "列表验证禁止写入" };
    } else if (pathname === "/api/me") body = user;
    else if (pathname === "/api/auth/options") body = { wecom: false };
    else if (pathname === "/api/directory")
      body = {
        users: [
          ...names.map((name, index) => ({
            ...user,
            id: `isolated-person-${index}`,
            name,
          })),
          { ...user, id: "isolated-verifier", name: "周经理" },
        ],
        departments: [],
        series: [],
      };
    else if (pathname === "/api/tasks") {
      controls.queries.push(url.searchParams);
      if (controls.failed) {
        status = 503;
        body = { detail: "隔离模拟任务读取失败" };
      } else body = options.empty ? [] : tasks;
    } else if (/^\/api\/tasks\/[^/]+\/native-todo$/.test(pathname))
      body = { enabled: false, link: null };
    else {
      const id = pathname.match(/^\/api\/tasks\/([^/]+)$/)?.[1];
      const task = tasks.find((row) => row.id === id);
      if (task) {
        controls.details.push(id!);
        body = {
          ...task,
          version: 3,
          requirements: "逐项核对完成结果，保留验收依据。",
          deliverable: "任务验收记录",
          requests: [],
          versions: [],
          closed_at: null,
          can_edit: false,
          can_transfer: false,
        };
      } else {
        controls.unexpected.push(`${request.method()} ${pathname}`);
        status = 404;
        body = { detail: "未预期的隔离请求" };
      }
    }
    await route.fulfill({
      status,
      contentType: "application/json",
      body: JSON.stringify(body),
    });
  });
  await page.goto("/tasks");
  await expect(
    page.getByRole("heading", { name: "我的待办", exact: true }),
  ).toBeVisible();
  return controls;
}

function expectReadOnly(controls: Awaited<ReturnType<typeof mockTasks>>) {
  expect(controls.writes).toEqual([]);
  expect(controls.unexpected).toEqual([]);
  expect(controls.pageErrors).toEqual([]);
}

async function expectNoPageOverflow(page: Page) {
  await expect
    .poll(() =>
      page.evaluate(() => ({
        width: window.innerWidth,
        document: document.documentElement.scrollWidth,
        body: document.body.scrollWidth,
      })),
    )
    .toEqual({
      width: page.viewportSize()!.width,
      document: page.viewportSize()!.width,
      body: page.viewportSize()!.width,
    });
}

test("桌面日期和会议名称在前，关闭项隐藏，长内容与多人可读且两处入口打开原详情抽屉", async ({
  page,
}) => {
  const controls = await mockTasks(page);
  const table = page.getByRole("table");
  await expect(table).toBeVisible();
  await expect(table.getByRole("columnheader")).toHaveText([
    "会议日期",
    "会议名称",
    "任务内容",
    "截止日期",
    "责任人",
    "验证人",
    "状态",
    "操作",
  ]);
  await expect(table.locator("tbody tr")).toHaveCount(visibleTasks.length);
  await expect(page.locator(".task-mobile-list")).not.toBeVisible();
  await expect(page.locator(".task-card")).toHaveCount(0);
  const longRow = table.getByRole("row").filter({ hasText: tasks[1].content });
  await expect(longRow).toContainText(longContent);
  await expect(longRow).toContainText(names.join("、"));
  await expect(longRow).toContainText("由 部门负责人 派发");
  await expect(longRow).toContainText("已提交申请 · 待验证");
  await expect(
    longRow.getByRole("cell", {
      name: "直接派发 由 部门负责人 派发",
      exact: true,
    }),
  ).toBeVisible();
  await expect(
    longRow.getByRole("cell", { name: tasks[1].due_date!, exact: true }),
  ).toBeVisible();
  const meetingRow = table
    .getByRole("row")
    .filter({ hasText: tasks[0].content });
  await expect(meetingRow).toContainText(tasks[0].source_meeting_title!);
  await expect(
    meetingRow.getByRole("cell", { name: "2026-10-21", exact: true }),
  ).toBeVisible();
  await expect(meetingRow).not.toContainText("2026-10-25");
  const hiddenSourceRow = table
    .getByRole("row")
    .filter({ hasText: tasks[4].content });
  await expect(hiddenSourceRow).toContainText("来源会议不可见");
  await expect(hiddenSourceRow).not.toContainText("直接派发");
  await expect(
    hiddenSourceRow.getByRole("cell", { name: "—", exact: true }).first(),
  ).toBeVisible();
  await expect(
    table.getByRole("row").filter({ hasText: tasks[2].content }),
  ).toContainText(tasks[2].source_meeting_title!);
  await expect(
    table.getByRole("row").filter({ hasText: tasks[3].content }),
  ).toContainText("未设期限");
  await expect(table).not.toContainText("已关闭任务不再展示");
  await expect(table).not.toContainText("RW-");
  await expect(meetingRow).toContainText("2026-10-21");
  await expect(meetingRow).toContainText("到期未验证");
  await expect(
    table.locator("tbody tr").filter({ hasText: "电子用印培训和试运行" }),
  ).toContainText("未到期验证");
  await expectNoPageOverflow(page);
  await page.screenshot({
    path: path.resolve(screenshotDir, "02-desktop-1440.png"),
    fullPage: true,
    animations: "disabled",
  });
  await page.setViewportSize({ width: 1920, height: 1080 });
  await expect(table).toBeVisible();
  await expectNoPageOverflow(page);
  await page.screenshot({
    path: path.resolve(screenshotDir, "01-wide-1920.png"),
    fullPage: true,
    animations: "disabled",
  });
  await page.setViewportSize({ width: 1024, height: 900 });
  await expect(table).toBeVisible();
  await expectNoPageOverflow(page);
  await page.setViewportSize({ width: 1440, height: 1000 });
  await table
    .getByRole("button", { name: tasks[0].content, exact: true })
    .click();
  const drawer = page.getByRole("dialog", { name: "任务详情", exact: true });
  await expect(drawer).toBeVisible();
  await expect(
    drawer.getByRole("heading", { name: tasks[0].content, exact: true }),
  ).toBeVisible();
  await expect(drawer.getByLabel("完成说明", { exact: true })).toBeVisible();
  await expect(
    drawer.getByRole("link", { name: "查看来源会议 →" }),
  ).toBeVisible();
  await drawer.locator(".el-drawer__close-btn").click();
  await expect(drawer).not.toBeVisible();
  const detailAction = longRow.getByRole("button", {
    name: `查看详情：${longContent}`,
    exact: true,
  });
  await detailAction.scrollIntoViewIfNeeded();
  await expect(detailAction).toBeInViewport();
  await expectNoPageOverflow(page);
  await detailAction.click();
  await expect(
    drawer.getByRole("heading", { name: longContent, exact: true }),
  ).toBeVisible();
  await expect(drawer).toContainText(names.join("、"));
  expect(controls.details).toEqual([tasks[0].id, tasks[1].id]);
  expectReadOnly(controls);
});

test("查询、状态和只看我的筛选保留原读取参数", async ({ page }) => {
  const controls = await mockTasks(page);
  await expect.poll(() => controls.queries.length).toBe(1);
  expect(controls.queries[0].get("mine")).toBe("true");
  expect(controls.queries[0].get("current")).toBe("true");
  await page
    .getByRole("textbox", { name: "搜索任务", exact: true })
    .fill("巡检");
  await page
    .getByRole("textbox", { name: "搜索任务", exact: true })
    .press("Enter");
  await expect.poll(() => controls.queries.at(-1)?.get("q")).toBe("巡检");
  await page.locator(".filter-row .el-select__wrapper").click();
  await page.getByRole("option", { name: "到期未验证", exact: true }).click();
  await expect
    .poll(() => controls.queries.at(-1)?.get("state"))
    .toBe("到期未验证");
  await page.locator(".filter-row .el-select__wrapper").click();
  await page.getByRole("option", { name: "未到期验证", exact: true }).click();
  await expect
    .poll(() => controls.queries.at(-1)?.get("state"))
    .toBe("未到期验证");
  await page.locator(".filter-row .el-select__wrapper").click();
  await page.getByRole("option", { name: "未完成", exact: true }).click();
  await expect.poll(() => controls.queries.at(-1)?.get("state")).toBe("未完成");
  await page.locator(".filter-row .el-switch__core").click();
  await expect.poll(() => controls.queries.at(-1)?.get("mine")).toBe("false");
  const before = controls.queries.length;
  await page.getByRole("button", { name: "查询", exact: true }).click();
  await expect.poll(() => controls.queries.length).toBe(before + 1);
  expect(controls.queries.at(-1)?.get("q")).toBe("巡检");
  expect(controls.queries.at(-1)?.get("state")).toBe("未完成");
  expectReadOnly(controls);
});

test("空结果展示清晰空状态，列表表格不留空壳", async ({ page }) => {
  const controls = await mockTasks(page, { empty: true });
  await expect(
    page.getByText("当前筛选下没有待办", { exact: true }),
  ).toBeVisible();
  await expect(page.getByText("共 0 条任务", { exact: true })).toBeVisible();
  await expect(page.getByRole("table")).toHaveCount(0);
  await expect(
    page.getByRole("list", { name: "待办任务", exact: true }),
  ).toHaveCount(0);
  expectReadOnly(controls);
});

test("读取失败可重试恢复列表", async ({ page }) => {
  const controls = await mockTasks(page, { failed: true });
  const alert = page.getByRole("alert").filter({ hasText: "待办列表加载失败" });
  await expect(alert).toContainText("隔离模拟任务读取失败");
  await expect(
    page.getByText("加载失败，请重试", { exact: true }),
  ).toBeVisible();
  controls.failed = false;
  await alert.getByRole("button", { name: "重试加载", exact: true }).click();
  await expect(alert).toHaveCount(0);
  await expect(page.getByRole("table").locator("tbody tr")).toHaveCount(
    visibleTasks.length,
  );
  expect(controls.queries).toHaveLength(2);
  expectReadOnly(controls);
});

test("手机使用紧凑逐项列表，长任务与多人无页面横向溢出", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const controls = await mockTasks(page);
  const list = page.getByRole("list", { name: "待办任务", exact: true });
  await expect(list).toBeVisible();
  await expect(list).not.toContainText("RW-");
  await expect(list).not.toContainText("已关闭任务不再展示");
  await expect(list.getByRole("listitem")).toHaveCount(visibleTasks.length);
  await expect(page.getByRole("table")).not.toBeVisible();
  const longRow = list
    .getByRole("listitem")
    .filter({ hasText: tasks[1].content });
  await expect(longRow).toContainText(longContent);
  await expect(longRow).toContainText(names.join("、"));
  await expect(longRow).toContainText("已提交申请 · 待验证");
  await expect(longRow.getByText("直接派发", { exact: true })).toBeVisible();
  await expect(longRow).not.toContainText("开始日期");
  await expect(longRow).toContainText(tasks[1].due_date!);
  const meetingRow = list
    .getByRole("listitem")
    .filter({ hasText: tasks[0].content });
  await expect(meetingRow).toContainText(tasks[0].source_meeting_title!);
  await expect(meetingRow).toContainText("2026-10-21");
  await expect(meetingRow).toContainText("到期未验证");
  await expect(
    list.getByRole("listitem").filter({ hasText: "电子用印培训和试运行" }),
  ).toContainText("未到期验证");
  await expect(meetingRow).not.toContainText("2026-10-25");
  const hiddenSourceRow = list
    .getByRole("listitem")
    .filter({ hasText: tasks[4].content });
  await expect(hiddenSourceRow).toContainText("来源会议不可见");
  await expect(hiddenSourceRow).not.toContainText("直接派发");
  await expect(
    hiddenSourceRow.getByText("—", { exact: true }).first(),
  ).toBeVisible();
  await expectNoPageOverflow(page);
  await page.screenshot({
    path: path.resolve(screenshotDir, "03-mobile-390.png"),
    fullPage: true,
    animations: "disabled",
  });
  await longRow
    .getByRole("button", { name: `查看详情：${longContent}`, exact: true })
    .click();
  const drawer = page.getByRole("dialog", { name: "任务详情", exact: true });
  await expect(
    drawer.getByRole("heading", { name: longContent, exact: true }),
  ).toBeVisible();
  await expectNoPageOverflow(page);
  await drawer.locator(".el-drawer__close-btn").click();
  await page.setViewportSize({ width: 320, height: 740 });
  await expect(list).toBeVisible();
  await expectNoPageOverflow(page);
  expectReadOnly(controls);
});
