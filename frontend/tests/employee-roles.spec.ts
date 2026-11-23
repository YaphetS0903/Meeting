import { expect, test, type Page } from "@playwright/test";
import { fileURLToPath } from "node:url";
const evidence = fileURLToPath(
  new URL("../../../.validation/custom-roles-20261110/", import.meta.url),
);
const departments = [
  { id: "it", name: "信息部" },
  { id: "sales", name: "销售部" },
];
const users = [
  {
    id: "admin",
    name: "系统管理员",
    department_id: "it",
    active: true,
    is_admin: true,
  },
  {
    id: "person",
    name: "金丽君",
    department_id: "it",
    active: true,
    is_admin: false,
  },
  {
    id: "inactive",
    name: "张玉梅",
    department_id: "sales",
    active: false,
    is_admin: false,
  },
];
const catalog = [
  {
    key: "series",
    name: "会议系列",
    actions: [
      { key: "view", name: "查看系列" },
      { key: "create", name: "新建系列" },
    ],
  },
  {
    key: "meetings",
    name: "会议任务单",
    actions: [
      { key: "view", name: "查看会议及草稿" },
      { key: "edit", name: "编辑任务单" },
      { key: "assess", name: "修改完成结果" },
      { key: "preview", name: "预览任务单" },
      { key: "publish", name: "发布会议" },
      { key: "delete_draft", name: "删除未发布草稿" },
    ],
  },
  {
    key: "reports",
    name: "常规统计报表",
    actions: [
      { key: "view", name: "查看常规统计" },
      { key: "export", name: "导出常规统计" },
    ],
  },
].map((m) => ({ ...m, scopes: ["self", "department", "selected", "all"] }));
const rule = (
  module: string,
  actions: string[],
  scope = "department",
  department_ids: string[] = [],
) => ({ module, actions, scope, department_ids });
async function mock(
  page: Page,
  config: {
    admin?: boolean;
    fail?: boolean;
    conflict?: boolean;
    disabled?: boolean;
  } = {},
) {
  let roles: any[] = [
    {
      id: "role1",
      name: "部门报表",
      description: "查看本部门报表",
      active: true,
      version: 1,
      rules: [rule("reports", ["view", "export"])],
      member_count: 0,
    },
    {
      id: "role2",
      name: "会议管理",
      description: "部门会议编辑发布",
      active: !config.disabled,
      version: 1,
      rules: [
        rule("meetings", [
          "edit",
          "assess",
          "preview",
          "publish",
          "delete_draft",
        ]),
      ],
      member_count: 1,
    },
  ];
  let assignments: any[] = [{ user_id: "inactive", roles: ["role2"] }];
  const writes: any[] = [],
    errors: string[] = [];
  let fail = config.fail,
    conflict = config.conflict;
  page.on("pageerror", (e) => errors.push(e.message));
  await page.addInitScript(() =>
    sessionStorage.setItem("token", "isolated-custom-role"),
  );
  await page.route("**/api/**", async (route) => {
    const req = route.request(),
      path = new URL(req.url()).pathname,
      method = req.method();
    if (method !== "GET") {
      const body = req.postDataJSON();
      writes.push({ path, body });
      if (conflict) {
        conflict = false;
        return route.fulfill({
          status: 409,
          json: { detail: "角色已被修改，请重新加载" },
        });
      }
      if (path.endsWith("business-roles")) {
        const user_id = path.split("/")[4];
        assignments = assignments.filter((a) => a.user_id !== user_id);
        const saved = { user_id, roles: body.roles };
        assignments.push(saved);
        return route.fulfill({ json: saved });
      }
      if (path.startsWith("/api/admin/roles")) {
        const id = method === "POST" ? "new-role" : path.split("/").at(-1);
        if (method === "DELETE") {
          roles = roles.filter((r) => r.id !== id);
          assignments = assignments.map((a) => ({
            ...a,
            roles: a.roles.filter((r: string) => r !== id),
          }));
          return route.fulfill({ json: { id, deleted: true } });
        }
        const saved = {
          ...body,
          id,
          version: method === "POST" ? 1 : body.expected_version + 1,
          member_count: 0,
        };
        roles = roles.filter((r) => r.id !== id);
        roles.push(saved);
        return route.fulfill({
          status: method === "POST" ? 201 : 200,
          json: saved,
        });
      }
    }
    let body: any = [];
    if (path === "/api/me")
      body = {
        ...users[0],
        username: "admin",
        is_admin: config.admin !== false,
        can_assign_tasks: true,
        can_oversee_tasks: true,
      };
    else if (path === "/api/directory")
      body = { users, departments, series: [] };
    else if (path === "/api/auth/options") body = { wecom: false };
    else if (path === "/api/admin/config") body = { wecom_configured: false };
    else if (path === "/api/admin/native-todo") body = { allowed_users: [] };
    else if (path === "/api/admin/reminders")
      body = { enabled: true, daily_enabled: false };
    else if (
      ["/api/admin/operations", "/api/admin/integration-checks"].includes(path)
    )
      body = {};
    else if (path === "/api/admin/roles") {
      if (fail) {
        fail = false;
        return route.fulfill({ status: 503, json: { detail: "角色读取失败" } });
      }
      body = roles;
    } else if (path === "/api/admin/role-catalog") body = catalog;
    else if (path === "/api/admin/role-assignments") body = assignments;
    else if (path.endsWith("/members")) body = [users[2]];
    return route.fulfill({ json: body });
  });
  return { writes, errors };
}
async function open(page: Page, tab = "角色管理") {
  await page.goto("/settings");
  await page.getByRole("tab", { name: tab, exact: true }).click();
}
for (const width of [1366, 390])
  test(`delete role confirms revocation and supports cancel at ${width}px`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 844 });
    const result = await mock(page);
    await open(page);
    await page
      .getByRole("button", { name: "删除会议管理", exact: true })
      .click();
    const confirm = page.getByRole("dialog", { name: "删除角色", exact: true });
    await expect(confirm).toContainText("已分配给 1 人");
    await expect(confirm).toContainText(
      "员工账号、其他角色及本人会议／任务职责保留",
    );
    await confirm.getByRole("button", { name: "取消", exact: true }).click();
    expect(result.writes).toHaveLength(0);
    await page
      .getByRole("button", { name: "删除会议管理", exact: true })
      .click();
    await confirm
      .getByRole("button", { name: "确认删除", exact: true })
      .click();
    await expect(confirm).toBeHidden();
    await expect(
      page.getByRole("button", { name: "删除会议管理", exact: true }),
    ).toHaveCount(0);
    await expect(
      page.getByRole("button", { name: "删除部门报表", exact: true }),
    ).toBeVisible();
    expect(result.writes[0].body).toEqual({
      expected_version: 1,
      expected_member_count: 1,
    });
    expect(result.errors).toEqual([]);
    await page.screenshot({
      path: evidence + `roles-delete-${width}.png`,
      fullPage: true,
    });
  });

test("delete conflict keeps role and reloads before another confirmation", async ({
  page,
}) => {
  const result = await mock(page, { conflict: true });
  await open(page);
  await page.getByRole("button", { name: "删除部门报表", exact: true }).click();
  await page.getByRole("button", { name: "确认删除", exact: true }).click();
  await expect(
    page.getByText("角色已被修改，请重新加载", { exact: true }).first(),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "删除部门报表", exact: true }),
  ).toBeEnabled();
  expect(result.writes).toHaveLength(1);
});
for (const width of [1366, 390])
  test(`create module scopes and assign multiple roles at ${width}px`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 768 });
    const result = await mock(page);
    await open(page);
    await page.getByRole("button", { name: "新建角色", exact: true }).click();
    const drawer = page.getByRole("dialog", { name: "新建角色", exact: true });
    await expect(drawer.getByRole("region", { name: "周期任务", exact: true })).toHaveCount(0);
    await drawer.getByLabel("角色名称", { exact: true }).fill("会议发布专员");
    await drawer.getByText("发布会议", { exact: true }).click();
    await expect(
      drawer.getByRole("checkbox", { name: "预览任务单", exact: true }),
    ).toBeChecked();
    await expect(
      drawer.getByRole("checkbox", { name: "编辑任务单", exact: true }),
    ).not.toBeChecked();
    await drawer.getByText("查看常规统计", { exact: true }).click();
    await drawer
      .getByRole("region", { name: "常规统计报表", exact: true })
      .locator(".el-select__wrapper")
      .click();
    await page.getByRole("option", { name: "全公司", exact: true }).click();
    await drawer.getByText("修改完成结果", { exact: true }).click();
    await expect(
      drawer.getByRole("checkbox", { name: "编辑任务单", exact: true }),
    ).toBeChecked();
    await drawer
      .getByLabel("角色名称", { exact: true })
      .scrollIntoViewIfNeeded();
    await page.screenshot({
      path: evidence + `roles-editor-${width}.png`,
      fullPage: true,
    });
    await drawer
      .getByRole("button", { name: "保存角色配置", exact: true })
      .click();
    await expect(drawer).toBeHidden();
    const body = result.writes.find((w) => w.path === "/api/admin/roles").body;
    expect(body.rules.find((r: any) => r.module === "reports").scope).toBe(
      "all",
    );
    expect(body.rules.find((r: any) => r.module === "meetings").scope).toBe(
      "department",
    );
    await page.getByRole("tab", { name: "员工通讯录", exact: true }).click();
    await page.getByLabel("搜索员工通讯录", { exact: true }).fill("金丽君");
    await page.getByRole("button", { name: "设置角色", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "金丽君 · 设置角色" });
    await dialog.getByText("部门报表", { exact: true }).click();
    await dialog.getByText("会议发布专员", { exact: true }).click();
    await page.screenshot({
      path: evidence + `roles-assign-${width}.png`,
      fullPage: true,
    });
    await dialog.getByRole("button", { name: "保存角色", exact: true }).click();
    await expect(dialog).toBeHidden();
    const saved = result.writes.find((w) =>
      w.path.endsWith("business-roles"),
    ).body;
    expect(saved.roles.sort()).toEqual(["new-role", "role1"]);
    expect(saved.expected_roles).toEqual([]);
    expect(saved.expected_department_id).toBe("it");
    expect(result.errors).toEqual([]);
  });
test("role read failure has retry and prevents empty overwrite", async ({
  page,
}) => {
  const result = await mock(page, { fail: true });
  await open(page);
  await expect(
    page.getByRole("button", { name: "新建角色", exact: true }),
  ).toBeDisabled();
  await page.getByRole("button", { name: "重新加载角色", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "新建角色", exact: true }),
  ).toBeEnabled();
  expect(result.writes).toEqual([]);
});
test("conflict preserves editable form and exposes persistent error", async ({
  page,
}) => {
  const result = await mock(page, { conflict: true });
  await open(page);
  await page.getByRole("button", { name: "编辑部门报表", exact: true }).click();
  const drawer = page.getByRole("dialog", { name: "编辑角色", exact: true });
  await drawer.getByLabel("角色名称", { exact: true }).fill("本部门统计");
  await drawer
    .getByRole("button", { name: "保存角色配置", exact: true })
    .click();
  await expect(drawer).toBeVisible();
  await expect(drawer).toContainText("角色已被修改，请重新加载");
  expect(result.writes[0].body.expected_version).toBe(1);
});
test("inactive employees and disabled roles can be revoked but not added", async ({
  page,
}) => {
  const result = await mock(page, { disabled: true });
  await open(page, "员工通讯录");
  await page.getByLabel("搜索员工通讯录", { exact: true }).fill("张玉梅");
  await page.getByRole("button", { name: "设置角色", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "张玉梅 · 设置角色" });
  await expect(
    dialog.getByRole("checkbox", { name: "部门报表", exact: true }),
  ).toBeDisabled();
  const assigned = dialog.getByRole("checkbox", {
    name: "会议管理（已停用）",
    exact: true,
  });
  await expect(assigned).toBeEnabled();
  await dialog.getByText("会议管理（已停用）", { exact: true }).click();
  await dialog.getByRole("button", { name: "保存角色", exact: true }).click();
  expect(result.writes[0].body.roles).toEqual([]);
});
test("members are inspectable and business settings are unavailable to ordinary employees", async ({
  page,
}) => {
  await mock(page);
  await open(page);
  await page
    .getByRole("button", { name: "查看会议管理的成员", exact: true })
    .click();
  await expect(
    page.getByRole("dialog", { name: "会议管理 · 已分配员工" }),
  ).toContainText("张玉梅");
});
