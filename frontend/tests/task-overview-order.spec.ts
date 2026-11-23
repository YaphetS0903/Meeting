import { expect, test, type Page } from "@playwright/test";
import { fileURLToPath } from "node:url";

const evidence = fileURLToPath(
  new URL("../../../.validation/task-labels-20261106/", import.meta.url),
);
const departments = [
  { id: "it", name: "信息部" },
  { id: "finance", name: "财务部" },
];
const users = [
  {
    id: "admin",
    name: "系统管理员",
    active: true,
    is_admin: true,
    can_oversee_tasks: true,
  },
  { id: "wang", name: "王晨", active: true, department_id: "it" },
  { id: "robot", name: "RPA机器人01", active: true, department_id: "it" },
  { id: "test-account", name: "ceshi", active: true, department_id: "it" },
];
const items = Array.from({ length: 25 }, (_, index) => ({
  id: `task-${index}`,
  code: `RW-${index}`,
  content: `系统优化工作事项${index + 1}`,
  department_names: index < 22 ? ["信息部"] : ["财务部"],
  due_date: index < 20 ? `2026-11-${String(3 + index).padStart(2, "0")}` : null,
  owner_names: index % 3 === 0 ? ["王晨", "袁明"] : ["王晨"],
  verifier_name: "朱燕玲",
  issuer_name: null,
  status: "未到期",
  pending: false,
  source_title: index === 1 ? "直接派发" : "系统相关优化方案讨论周工作会议",
  source_meeting_date: index === 1 ? null : "2026-11-02",
}));

async function mock(page: Page) {
  const calls: URL[] = [],
    errors: string[] = [],
    writes: string[] = [];
  const control = { fail: false, empty: false };
  page.on("pageerror", (error) => errors.push(error.message));
  await page.addInitScript(() =>
    sessionStorage.setItem("token", "isolated-overview-test"),
  );
  await page.route("**/api/**", async (route) => {
    const request = route.request(),
      url = new URL(request.url());
    if (request.method() !== "GET")
      writes.push(request.method() + " " + url.pathname);
    let body: any = [];
    if (url.pathname === "/api/me") body = users[0];
    else if (url.pathname === "/api/directory")
      body = { users, departments, series: [] };
    else if (url.pathname === "/api/auth/options") body = { wecom: false };
    else if (url.pathname === "/api/task-overview/options")
      body = { scope: "all", users, departments };
    else if (url.pathname === "/api/task-overview") {
      calls.push(url);
      if (control.fail) {
        control.fail = false;
        return route.fulfill({
          status: 503,
          json: { detail: "暂时无法读取任务" },
        });
      }
      const offset = Number(url.searchParams.get("offset") || 0);
      const filtered = control.empty
        ? []
        : items.filter(
            (row) =>
              !url.searchParams.get("department_id") ||
              row.department_names.includes("财务部"),
          );
      body = {
        items: filtered.slice(offset, offset + 20),
        total: filtered.length,
        summary: { total: filtered.length },
      };
    }
    return route.fulfill({ json: body });
  });
  return { calls, errors, writes, control };
}

test("总览责任人筛选隐藏非人员账号，任务记录仍保留", async ({ page }) => {
  await mock(page);
  await page.goto("/task-overview");
  const input = page.getByRole("combobox", { name: "筛选责任人", exact: true });
  for (const name of ["RPA机器人01", "ceshi"]) {
    await input.fill(name);
    await expect(
      page.getByRole("option", { name: new RegExp(name) }),
    ).toHaveCount(0);
  }
  await input.fill("王晨");
  await expect(page.getByRole("option", { name: /王晨/ })).toBeVisible();
});

for (const width of [1440, 1366, 390]) {
  test(`overview exposes departments, deadlines and people at ${width}px`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 1000 });
    const result = await mock(page);
    await page.goto("/task-overview");
    await expect(
      page.getByRole("heading", { name: "任务总览", exact: true }),
    ).toBeVisible();
    await expect(
      page.getByText("按部门、截止日期（近到远）、责任人排序"),
    ).toBeVisible();
    if (width > 767) {
      const headers = await page
        .locator(".overview-desktop thead th")
        .allTextContents();
      expect(headers.slice(0, 4)).toEqual([
        "所属部门",
        "截止日期",
        "责任人",
        "任务内容",
      ]);
      const first = page
        .locator(".overview-desktop .el-table__body-wrapper tbody tr")
        .first();
      await expect(first).toContainText("信息部");
      await expect(first).toContainText("2026-11-03");
      await expect(first).toContainText("王晨、袁明");
      await expect(first).toContainText(
        "2026-11-02 · 系统相关优化方案讨论周工作会议",
      );
    } else {
      const first = page.locator(".overview-mobile-task").first();
      await expect(first).toContainText("信息部");
      await expect(first).toContainText("截止日期：2026-11-03");
      await expect(first).toContainText("责任人：王晨、袁明");
      await expect(first).toContainText(
        "2026-11-02 · 系统相关优化方案讨论周工作会议",
      );
    }
    const results = page.locator(
      width > 767 ? ".overview-desktop" : ".overview-mobile",
    );
    await expect(results).not.toContainText("RW-");
    await expect(results).toContainText("直接派发");
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    ).toBe(true);
    await page.screenshot({
      path: `${evidence}/overview-${width}.png`,
      fullPage: true,
      animations: "disabled",
    });
    expect(result.errors).toEqual([]);
    expect(result.writes).toEqual([]);
  });
}

test("pagination preserves filters and department filtering returns to first page", async ({
  page,
}) => {
  const result = await mock(page);
  await page.goto("/task-overview");
  await page.getByRole("button", { name: "下一页", exact: true }).click();
  await expect
    .poll(() => result.calls.at(-1)?.searchParams.get("offset"))
    .toBe("20");
  await expect(
    page.locator(".overview-desktop .el-table__body-wrapper tbody tr"),
  ).toHaveCount(5);
  await expect(page.locator(".overview-desktop")).toContainText("未设期限");
  await page.getByRole("combobox", { name: "筛选部门（包含子部门）" }).click();
  await page.getByRole("option", { name: "财务部", exact: true }).click();
  await expect
    .poll(() => result.calls.at(-1)?.searchParams.get("offset"))
    .toBe("0");
  expect(result.calls.at(-1)?.searchParams.get("department_id")).toBe(
    "finance",
  );
  await expect(
    page.locator(".overview-desktop .el-table__body-wrapper tbody tr"),
  ).toHaveCount(3);
  await page.getByRole("button", { name: "清空筛选", exact: true }).click();
  await expect
    .poll(() => result.calls.at(-1)?.searchParams.has("department_id"))
    .toBe(false);
  expect(result.errors).toEqual([]);
});

test("empty and failed requests offer clear recovery", async ({ page }) => {
  const result = await mock(page);
  result.control.fail = true;
  await page.goto("/task-overview");
  await expect(
    page.getByText("任务进展加载失败：暂时无法读取任务"),
  ).toBeVisible();
  await page.getByRole("button", { name: "重试加载", exact: true }).click();
  await expect(
    page.locator(".overview-desktop .el-table__body-wrapper tbody tr"),
  ).toHaveCount(20);
  result.control.empty = true;
  await page.getByRole("button", { name: "刷新进展", exact: true }).click();
  await expect(
    page.locator(".overview-desktop").getByText("当前筛选范围没有任务"),
  ).toBeVisible();
  expect(result.errors).toEqual([]);
  expect(result.writes).toEqual([]);
});
