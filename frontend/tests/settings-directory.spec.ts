import { expect, test, type Page } from "@playwright/test";
import path from "node:path";
import { fileURLToPath } from "node:url";

const evidence = fileURLToPath(
  new URL(
    "../../../.validation/employee-directory-tab-20261102/",
    import.meta.url,
  ),
);
const departments = [{ id: "it", name: "信息部", parent_id: null }];
const originalUsers = [
  {
    id: "member",
    name: "张员工",
    email: "zhang@example.com",
    department_id: "it",
    active: true,
    is_admin: false,
  },
  {
    id: "disabled",
    name: "离职员工",
    department_id: "it",
    active: false,
    is_admin: false,
  },
  {
    id: "admin",
    name: "系统管理员",
    department_id: "it",
    active: true,
    is_admin: true,
  },
];

async function mock(
  page: Page,
  admin = true,
  failHistory = false,
  additionalUsers: any[] = [],
) {
  let users = structuredClone([...originalUsers, ...additionalUsers]);
  const calls: string[] = [];
  const writes: any[] = [];
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.addInitScript(() =>
    sessionStorage.setItem("token", "isolated-settings-test"),
  );
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    calls.push(url.pathname);
    let body: any = [];
    if (request.method() !== "GET") {
      expect(url.pathname).toBe("/api/admin/users/member/role");
      const payload = request.postDataJSON();
      writes.push(payload);
      users = users.map((user) =>
        user.id === "member" ? { ...user, is_admin: payload.is_admin } : user,
      );
      return route.fulfill({ json: {} });
    }
    if (url.pathname === "/api/me")
      body = users.find((user) => user.id === (admin ? "admin" : "member"));
    else if (url.pathname === "/api/directory")
      body = { users, departments, series: [] };
    else if (url.pathname === "/api/auth/options") body = { wecom: false };
    else if (url.pathname === "/api/admin/config")
      body = { wecom_configured: false };
    else if (url.pathname === "/api/admin/native-todo")
      body = { allowed_users: [] };
    else if (url.pathname === "/api/admin/reminders")
      body = { enabled: true, daily_enabled: false };
    else if (
      ["/api/admin/operations", "/api/admin/integration-checks"].includes(
        url.pathname,
      )
    )
      body = {};
    else if (url.pathname === "/api/admin/directory-sync-history") {
      if (failHistory) {
        failHistory = false;
        return route.fulfill({
          status: 503,
          json: { detail: "同步记录暂时不可用" },
        });
      }
      body = Array.from({ length: 60 }, (_, index) => ({
        id: String(index),
        created_at: `2026-11-02T${String(23 - (index % 24)).padStart(2, "0")}:00:00+08:00`,
        actor_name: "系统自动同步",
        state: "succeeded",
        result: { users: 583, departments: 120 },
      }));
    }
    return route.fulfill({ json: body });
  });
  return { calls, writes, errors };
}

test("employee list hides service accounts and keeps all disabled people last", async ({
  page,
}) => {
  const serviceNames = [
    "RPA机器人01",
    "RPA机器人02",
    "ceshi",
    "cs",
    "it机器人",
    "oa",
    "seeyon02",
    "管理员",
    "测试",
    "董事长信箱",
  ];
  const result = await mock(page, true, false, [
    { id: "disabled-admin", name: "停用管理员", active: false, is_admin: true },
    ...serviceNames.map((name, index) => ({
      id: `service-${index}`,
      name,
      active: true,
      is_admin: false,
    })),
    { id: "virtual", name: "入门申请", position: "虚拟岗位", active: true },
    { id: "english", name: "Jane Smith", active: true },
    {
      id: "engineer",
      name: "谢龙兵",
      position: "机器人设备工程师",
      active: true,
    },
  ]);
  await page.goto("/settings");
  await page.getByRole("tab", { name: "员工通讯录", exact: true }).click();
  const rows = page.locator(".el-table__body-wrapper tbody tr");
  await expect(rows).toHaveCount(6);
  await expect(rows.first()).toContainText("系统管理员");
  await expect(rows.nth(2)).toContainText("Jane Smith");
  await expect(rows.nth(3)).toContainText("谢龙兵");
  await expect(rows.nth(4)).toContainText("离职员工");
  await expect(rows.last()).toContainText("停用管理员");
  const search = page.getByRole("textbox", { name: "搜索员工通讯录" });
  for (const name of [...serviceNames, "入门申请"]) {
    await search.fill(name);
    await expect(page.getByRole("cell", { name, exact: true })).toHaveCount(0);
  }
  await search.fill("停用管理员");
  await expect(rows).toHaveCount(1);
  await expect(
    rows.first().getByRole("cell", { name: "已停用", exact: true }),
  ).toBeVisible();
  expect(result.writes).toEqual([]);
  expect(result.errors).toEqual([]);
});

for (const width of [1440, 390]) {
  test(`settings separates employees and collapses sync history at ${width}px`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 900 });
    const result = await mock(page);
    await page.goto("/settings");
    await page.getByRole("tab", { name: "通讯录同步", exact: true }).click();
    await expect(
      page.getByRole("heading", { name: "新增人员别名" }),
    ).toHaveCount(0);
    await expect(
      page.getByRole("heading", { name: "已保存的人员别名" }),
    ).toHaveCount(0);
    await expect(
      page.getByRole("columnheader", { name: "系统角色" }),
    ).toHaveCount(0);
    const history = page.locator("details");
    await expect(history).not.toHaveAttribute("open");
    expect(result.calls).not.toContain("/api/admin/directory-sync-history");
    expect(result.calls).not.toContain("/api/admin/aliases");
    await history.locator("summary").focus();
    await page.keyboard.press("Enter");
    await expect(
      history.getByRole("cell", { name: "系统自动同步", exact: true }),
    ).toHaveCount(60);
    expect(
      await history
        .locator(".el-table")
        .evaluate((el) => el.getBoundingClientRect().height),
    ).toBeLessThanOrEqual(421);
    await page.screenshot({
      path: path.join(evidence, `sync-history-${width}.png`),
      fullPage: true,
    });
    await history.locator("summary").click();
    await expect(history.getByRole("table")).toHaveCount(0);
    await expect(
      page
        .locator(".page-heading")
        .getByRole("button", { name: "员工通讯录", exact: true }),
    ).toHaveCount(0);
    const tabs = await page.getByRole("tab").allTextContents();
    expect(tabs.indexOf("员工通讯录")).toBe(tabs.indexOf("通讯录同步") + 1);
    await page.getByRole("tab", { name: "员工通讯录", exact: true }).click();
    await expect(page).toHaveURL(/\/settings$/);
    await expect(
      page.getByRole("heading", { name: "员工通讯录", exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole("heading", { name: "通讯录同步记录" }),
    ).not.toBeVisible();
    const rows = page.locator(".el-table__body-wrapper tbody tr");
    await expect(rows.nth(0)).toContainText("系统管理员");
    await expect(rows.nth(2)).toContainText("离职员工");
    for (const button of await rows
      .nth(2)
      .locator("button:not(.el-table__expand-icon)")
      .all())
      await expect(button).toBeDisabled();
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBeTruthy();
    await page.screenshot({
      path: path.join(evidence, `employees-${width}.png`),
      fullPage: true,
    });
    await page
      .getByRole("textbox", { name: "搜索员工通讯录" })
      .fill("zhang@example.com");
    await expect(rows).toHaveCount(1);
    await expect(rows.first()).toContainText("张员工");
    const name = rows
      .first()
      .getByRole("cell", { name: "张员工", exact: true });
    expect(
      await name.evaluate((el) => {
        const range = document.createRange();
        range.selectNodeContents(el);
        const selection = getSelection()!;
        selection.removeAllRanges();
        selection.addRange(range);
        return selection.toString();
      }),
    ).toContain("张员工");
    await page.getByRole("tab", { name: "通讯录同步", exact: true }).click();
    await expect(page).toHaveURL(/\/settings$/);
    expect(result.writes).toEqual([]);
    expect(result.errors).toEqual([]);
  });
}

test("employee directory keeps role updates and a direct-entry return path", async ({
  page,
}) => {
  const result = await mock(page);
  await page.goto("/settings/employees");
  await expect(
    page.getByRole("heading", { name: "员工通讯录", exact: true }),
  ).toBeVisible();
  await page.getByRole("textbox", { name: "搜索员工通讯录" }).fill("张员工");
  await page.getByRole("button", { name: "设为管理员", exact: true }).click();
  await page.locator(".el-message-box input").fill("测试角色调整");
  await page
    .locator(".el-message-box")
    .getByRole("button", { name: "确定", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "设为普通成员", exact: true }),
  ).toBeVisible();
  expect(result.writes).toEqual([
    { is_admin: true, expected_is_admin: false, reason: "测试角色调整" },
  ]);
  await page.getByRole("button", { name: "返回", exact: true }).click();
  await expect(page).toHaveURL(/\/settings$/);
  expect(result.errors).toEqual([]);
});

test("sync history failure can be retried without blocking settings", async ({
  page,
}) => {
  const result = await mock(page, true, true);
  await page.goto("/settings");
  await page.getByRole("tab", { name: "通讯录同步", exact: true }).click();
  await page.locator("summary").click();
  await expect(
    page.getByRole("alert").filter({ hasText: "同步记录暂时不可用" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "重新加载", exact: true }).click();
  await expect(
    page.getByRole("cell", { name: "系统自动同步", exact: true }),
  ).toHaveCount(60);
  expect(result.writes).toEqual([]);
  expect(result.errors).toEqual([]);
});

test("ordinary employees cannot use the admin directory route", async ({
  page,
}) => {
  const result = await mock(page, false);
  await page.goto("/settings/employees");
  await expect(
    page.getByText("仅系统管理员可查看员工通讯录", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("textbox", { name: "搜索员工通讯录" }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("cell", { name: "系统管理员", exact: true }),
  ).toHaveCount(0);
  expect(result.calls.filter((url) => url.startsWith("/api/admin/"))).toEqual(
    [],
  );
  expect(result.writes).toEqual([]);
  expect(result.errors).toEqual([]);
});
