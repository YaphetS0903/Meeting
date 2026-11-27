import { expect, test, type Page } from "@playwright/test";
import path from "node:path";

const departments = [
  { id: "company", name: "天瑞公司", parent_id: null },
  { id: "it", name: "信息部", parent_id: "company" },
  { id: "it-team", name: "开发组", parent_id: "it" },
  { id: "hr", name: "人力资源部", parent_id: "company" },
];
const users = [
  {
    id: "admin",
    name: "系统管理员",
    department_id: "it",
    active: true,
    is_admin: true,
  },
  { id: "lead", name: "部门负责人", department_id: "it", active: true },
  { id: "member", name: "开发员工", department_id: "it-team", active: true },
  { id: "delegate", name: "报表管理员甲", department_id: "hr", active: true },
  { id: "hr-user", name: "人资员工", department_id: "hr", active: true },
];
const counts = { 已完成: 1, 未完成: 0, 未到期: 0, 不统计: 0, 关闭: 0 };

async function mock(
  page: Page,
  mode: "all" | "departments" | "self",
  roots = ["it"],
) {
  const requests: URLSearchParams[] = [];
  const writes: any[] = [];
  const errors: string[] = [];
  let grants: any[] = [];
  let failAccess = false;
  let currentMode = mode;
  const me = mode === "all" ? users[0] : mode === "self" ? users[2] : users[1];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.addInitScript(() =>
    sessionStorage.setItem("token", "isolated-permission-test"),
  );
  await page.route("**/api/**", async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    let body: any = [];
    if (req.method() !== "GET") {
      const payload = req.postDataJSON();
      writes.push({ path: url.pathname, payload });
      expect(url.pathname).toBe("/api/admin/users/delegate/business-roles");
      grants = payload.roles.length
        ? [{ user_id: "delegate", roles: payload.roles }]
        : [];
      return route.fulfill({
        json: { user_id: "delegate", roles: payload.roles },
      });
    }
    if (url.pathname === "/api/me") body = me;
    else if (url.pathname === "/api/directory")
      body = { users, departments, series: [] };
    else if (url.pathname === "/api/auth/options") body = { wecom: false };
    else if (url.pathname === "/api/reports/access") {
      if (failAccess)
        return route.fulfill({
          status: 503,
          json: { detail: "报表权限读取失败" },
        });
      const ids =
        currentMode === "all"
          ? departments.map((d) => d.id)
          : currentMode === "self"
            ? []
            : roots.includes("it")
              ? ["it", "it-team"]
              : roots;
      body = {
        mode: currentMode,
        topics: [
          {
            key: "unassigned",
            name: "未归入系列",
            department_ids: ["it", "it-team", "hr"],
          },
        ],
        department_ids: ids,
        root_department_ids: roots,
        departments: departments.filter(
          (d) =>
            d.id === "company" ||
            (ids.includes("it-team") && d.id === "it") ||
            ids.includes(d.id),
        ),
        users:
          currentMode === "all"
            ? users
            : currentMode === "self"
              ? [me]
              : users.filter((u) => ids.includes(u.department_id)),
      };
    } else if (url.pathname === "/api/reports") {
      requests.push(url.searchParams);
      body = {
        quality: "已确认数据",
        summary: { items: 1, counts, rate: 1 },
        groups: [
          {
            id: me.id,
            label: me.name,
            active: true,
            items: 1,
            counts,
            rate: 1,
          },
        ],
        details: [],
      };
    } else if (url.pathname === "/api/admin/role-assignments") body = grants;
    else if (url.pathname === "/api/admin/roles")
      body = [
        {
          id: "report-role",
          name: "跨部门报表",
          description: "查看和导出信息部、人力资源部及其下属组织报表",
          active: true,
          version: 1,
          member_count: grants.length,
          rules: [
            {
              module: "reports",
              actions: ["view", "export"],
              scope: "selected",
              department_ids: ["it", "hr"],
            },
          ],
        },
      ];
    else if (
      ["/api/admin/config", "/api/admin/integration-checks"].includes(
        url.pathname,
      )
    )
      body = {};
    else if (url.pathname === "/api/admin/native-todo")
      body = { allowed_users: [] };
    else if (url.pathname === "/api/admin/reminders")
      body = { enabled: true, daily_enabled: false };
    else if (url.pathname === "/api/admin/operations") body = {};
    return route.fulfill({ json: body });
  });
  return {
    requests,
    writes,
    errors,
    fail: () => {
      failAccess = true;
    },
    revoke: () => {
      currentMode = "self";
    },
  };
}

test("部门角色只显示授权部门和下属员工，权限撤销后切回本人", async ({
  page,
}) => {
  const checks = await mock(page, "departments");
  await page.goto("/reports?view=regular");
  await expect.poll(() => checks.requests.length).toBe(1);
  const department = page.getByRole("combobox", {
    name: "筛选部门",
    exact: true,
  });
  await department.click();
  await expect(
    page.getByRole("option", { name: "天瑞公司 / 信息部", exact: true }),
  ).toBeVisible();
  await expect(page.getByRole("option", { name: /人力资源部/ })).toHaveCount(0);
  await page
    .getByRole("option", { name: "天瑞公司 / 信息部", exact: true })
    .click();
  await expect
    .poll(() => checks.requests.at(-1)?.get("department_id"))
    .toBe("it");
  const person = page.getByRole("combobox", { name: "筛选人员", exact: true });
  await person.fill("员工");
  await expect(
    page.getByRole("option", { name: "开发员工", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("option", { name: /人资员工|报表管理员甲/ }),
  ).toHaveCount(0);
  await page.keyboard.press("Escape");
  await page.screenshot({
    path: path.resolve(
      "../../.validation/report-access-in-roles-20261110/department-desktop.png",
    ),
    fullPage: true,
    animations: "disabled",
  });
  checks.revoke();
  await page
    .getByRole("combobox", { name: "筛选会议主题", exact: true })
    .press("ArrowDown");
  await page.getByRole("option", { name: "未归入系列", exact: true }).click();
  await expect(department).toHaveCount(0);
  await expect
    .poll(() => checks.requests.at(-1)?.get("person_id"))
    .toBe("lead");
  expect(checks.requests.at(-1)?.get("department_id")).toBeNull();
  expect(checks.writes).toEqual([]);
  expect(checks.errors).toEqual([]);
});

test("仅授权子组时不显示上级部门，权限读取失败不发报表查询", async ({
  page,
}) => {
  const checks = await mock(page, "departments", ["it-team"]);
  await page.goto("/reports?view=regular");
  await expect.poll(() => checks.requests.length).toBe(1);
  await page.getByRole("combobox", { name: "筛选部门", exact: true }).click();
  await expect(
    page.getByRole("option", { name: "天瑞公司 / 开发组", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("option", { name: "天瑞公司 / 信息部", exact: true }),
  ).toHaveCount(0);
  await page.keyboard.press("Escape");
  checks.fail();
  await page
    .getByRole("combobox", { name: "筛选会议主题", exact: true })
    .press("ArrowDown");
  await page.getByRole("option", { name: "未归入系列", exact: true }).click();
  await expect(
    page.getByText("报表加载失败：报表权限读取失败", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "导出 Excel", exact: true }),
  ).toBeDisabled();
  expect(checks.requests).toHaveLength(1);
  expect(checks.errors).toEqual([]);
});

test("通讯录通过配置好的跨部门报表角色授权和撤销，窄屏正常", async ({
  page,
}) => {
  const checks = await mock(page, "all");
  await page.setViewportSize({ width: 1366, height: 768 });
  await page.goto("/settings");
  await expect(
    page.getByRole("tab", { name: "报表权限", exact: true }),
  ).toHaveCount(0);
  await page.getByRole("tab", { name: "员工通讯录", exact: true }).click();
  await page
    .getByRole("textbox", { name: "搜索员工通讯录" })
    .fill("报表管理员甲");
  const person = page
    .locator(".directory-employees .el-table__body-wrapper tbody tr")
    .filter({ hasText: "报表管理员甲" });
  await person.getByRole("button", { name: "设置角色", exact: true }).click();
  const dialog = page.getByRole("dialog", {
    name: "报表管理员甲 · 设置角色",
    exact: true,
  });
  await dialog.getByText("跨部门报表", { exact: true }).click();
  await expect(
    dialog.getByRole("checkbox", { name: "跨部门报表", exact: true }),
  ).toBeChecked();
  await expect(dialog).toContainText(
    "查看和导出信息部、人力资源部及其下属组织报表",
  );
  await dialog.getByRole("button", { name: "保存角色", exact: true }).click();
  await expect(dialog).not.toBeVisible();
  expect(checks.writes[0].payload.roles).toEqual(["report-role"]);
  expect(checks.writes[0].payload.expected_roles).toEqual([]);
  await expect(person).toContainText("跨部门报表");
  await person.getByRole("button", { name: "设置角色", exact: true }).click();
  for (const width of [1366, 390]) {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 768 });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBeTruthy();
    const box = await dialog.boundingBox();
    expect(box!.x).toBeGreaterThanOrEqual(0);
    expect(box!.x + box!.width).toBeLessThanOrEqual(width);
    await page.screenshot({
      path: path.resolve(
        `../../.validation/custom-roles-20261110/report-assignment-${width}.png`,
      ),
      fullPage: true,
      animations: "disabled",
    });
  }
  await dialog.getByText("跨部门报表", { exact: true }).click();
  await expect(
    dialog.getByRole("checkbox", { name: "跨部门报表", exact: true }),
  ).not.toBeChecked();
  await dialog.getByRole("button", { name: "保存角色", exact: true }).click();
  await expect(dialog).not.toBeVisible();
  await expect(person).not.toContainText("跨部门报表");
  expect(checks.writes[1].payload.expected_roles).toEqual(["report-role"]);
  expect(checks.writes[1].payload.roles).toEqual([]);
  expect(checks.errors).toEqual([]);
});
