import { expect, test, type Page } from "@playwright/test";
import path from "node:path";

const departments = [
  { id: "root", name: "集团", parent_id: null },
  { id: "company-a", name: "天瑞公司", parent_id: "root" },
  { id: "company-b", name: "臻瑞公司", parent_id: "root" },
  { id: "support", name: "支持中心", parent_id: "company-a" },
  { id: "hr", name: "人力资源部", parent_id: "support" },
  { id: "hrbp", name: "HRBP组", parent_id: "hr" },
  { id: "it", name: "信息化", parent_id: "hr" },
  { id: "oa", name: "OA项目组", parent_id: "it" },
  { id: "hr-b", name: "人力资源部", parent_id: "company-b" },
  { id: "factory", name: "内饰工厂", parent_id: "company-a" },
  { id: "production", name: "生产", parent_id: "factory" },
  { id: "team", name: "生产班", parent_id: "production" },
  { id: "commerce", name: "商务中心", parent_id: "company-a" },
  { id: "commerce-team", name: "商务组", parent_id: "commerce" },
  { id: "commerce-b", name: "商务中心", parent_id: "company-b" },
  { id: "sales-b", name: "销售一部", parent_id: "commerce-b" },
];
const users = [
  { id: "admin", name: "管理员", department_id: "hr", active: true },
  { id: "hrbp-person", name: "HRBP员工", department_id: "hrbp", active: true },
  { id: "oa-person", name: "项目组员工", department_id: "oa", active: true },
  {
    id: "hr-b-person",
    name: "另一公司员工",
    department_id: "hr-b",
    active: true,
  },
  {
    id: "factory-person",
    name: "生产班员工",
    department_id: "team",
    active: true,
  },
  {
    id: "commerce-person",
    name: "商务员工",
    department_id: "commerce-team",
    active: true,
  },
  { id: "inactive", name: "已停用同事", department_id: "hr", active: false },
  {
    id: "commerce-b-person",
    name: "中心直属员工",
    department_id: "commerce-b",
    active: true,
  },
];
const counts = { 已完成: 1, 未完成: 0, 未到期: 0, 不统计: 0, 关闭: 0 };

async function mock(page: Page, admin = true) {
  const queries: URLSearchParams[] = [];
  const writes: string[] = [];
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.addInitScript(() =>
    sessionStorage.setItem("token", "isolated-report-departments"),
  );
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    let body: any;
    if (request.method() !== "GET") {
      writes.push(request.method() + " " + url.pathname);
      return route.fulfill({ status: 400, json: { detail: "只读页面检查" } });
    }
    if (url.pathname === "/api/me") body = { ...users[0], is_admin: admin };
    else if (url.pathname === "/api/directory")
      body = { users, departments, series: [] };
    else if (url.pathname === "/api/reports/access")
      body = {
        mode: admin ? "all" : "self",
        departments: admin ? departments : [],
        department_ids: admin ? departments.map((d) => d.id) : [],
        root_department_ids: [],
        users: admin ? users : [users[0]],
      };
    else if (url.pathname === "/api/auth/options") body = { wecom: false };
    else if (url.pathname === "/api/reports") {
      queries.push(url.searchParams);
      const roster = admin ? users.slice(0, 3) : [users[0]];
      body = {
        quality: "已确认数据",
        summary: {
          items: roster.length,
          counts: { ...counts, 已完成: roster.length },
          rate: 1,
        },
        groups: roster.map((person) => ({
          id: person.id,
          label: person.name,
          active: true,
          items: 1,
          counts,
          rate: 1,
        })),
        details: [],
      };
    } else
      return route.fulfill({ status: 404, json: { detail: "未预期请求" } });
    return route.fulfill({ json: body });
  });
  await page.goto("/reports");
  await expect(
    page.getByRole("heading", { name: "统计报表", exact: true }),
  ).toBeVisible();
  await expect.poll(() => queries.length).toBeGreaterThan(0);
  return { queries, writes, errors };
}

test("部门和工厂收拢细分组织，中心可选，同名公司分开，人员覆盖下属科组", async ({
  page,
}) => {
  const checks = await mock(page);
  const department = page.getByRole("combobox", {
    name: "筛选部门",
    exact: true,
  });
  await department.click();
  await expect(
    page.getByRole("option", { name: "臻瑞公司 / 商务中心", exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("option", { name: "臻瑞公司 / 销售一部", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("option", { name: "天瑞公司 / 人力资源部", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("option", { name: "臻瑞公司 / 人力资源部", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("option", { name: "天瑞公司 / 内饰工厂", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("option", { name: "天瑞公司 / 商务中心", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("option", {
      name: /HRBP组|OA项目组|信息化|生产班|商务组|支持中心/,
    }),
  ).toHaveCount(0);
  await page.screenshot({
    path: path.resolve(".validation/report-org-level-20261030/desktop.png"),
    fullPage: true,
    animations: "disabled",
  });
  await page
    .getByRole("option", { name: "天瑞公司 / 人力资源部", exact: true })
    .click();
  await expect
    .poll(() => checks.queries.at(-1)?.get("department_id"))
    .toBe("hr");
  await expect(
    page.getByRole("heading", {
      name: /天瑞公司 \/ 人力资源部 · 人员任务完成情况/,
    }),
  ).toBeVisible();
  const person = page.getByRole("combobox", { name: "筛选人员", exact: true });
  await person.fill("员工");
  await expect(
    page.getByRole("option", { name: "HRBP员工", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("option", { name: "项目组员工", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("option", {
      name: /另一公司员工|生产班员工|商务员工|已停用同事/,
    }),
  ).toHaveCount(0);
  await page.getByRole("option", { name: "项目组员工", exact: true }).click();
  await expect
    .poll(() => checks.queries.at(-1)?.get("person_id"))
    .toBe("oa-person");
  await department.click();
  await page
    .getByRole("option", { name: "天瑞公司 / 内饰工厂", exact: true })
    .click();
  await expect
    .poll(() => checks.queries.at(-1)?.get("department_id"))
    .toBe("factory");
  expect(checks.queries.at(-1)?.get("person_id")).toBeNull();
  await person.fill("员工");
  await expect(
    page.getByRole("option", { name: "生产班员工", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("option", { name: "项目组员工", exact: true }),
  ).toHaveCount(0);
  await page.keyboard.press("Escape");
  await page.setViewportSize({ width: 390, height: 844 });
  await expect
    .poll(() => page.evaluate(() => document.documentElement.scrollWidth))
    .toBe(390);
  await department.click();
  await page.screenshot({
    path: path.resolve(".validation/report-org-level-20261030/mobile.png"),
    fullPage: true,
    animations: "disabled",
  });
  expect(checks.writes).toEqual([]);
  expect(checks.errors).toEqual([]);
});

test("普通员工继续只查询本人，部门层级调整不暴露人员筛选", async ({ page }) => {
  const checks = await mock(page, false);
  await expect(
    page.getByRole("combobox", { name: "筛选部门", exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("combobox", { name: "筛选人员", exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByText("管理员（仅本人）", { exact: true }),
  ).toBeVisible();
  expect(checks.queries.at(-1)?.get("person_id")).toBe("admin");
  expect(checks.queries.at(-1)?.get("department_id")).toBeNull();
  expect(checks.writes).toEqual([]);
  expect(checks.errors).toEqual([]);
});
