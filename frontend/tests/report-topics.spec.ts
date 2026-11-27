import { expect, test, type Page } from "@playwright/test";
import path from "node:path";

async function fixture(page: Page, mode = "all") {
  const departments = [
    { id: "it", name: "信息部" },
    { id: "finance", name: "财务部" },
  ];
  const topics = [
    { key: "series:it-weekly", name: "信息部工作周会", department_ids: ["it"] },
    {
      key: "series:finance-weekly",
      name: "财务工作周会",
      department_ids: ["finance"],
    },
    {
      key: "series:shared",
      name: "跨部门项目会",
      department_ids: ["it", "finance"],
    },
  ];
  const me = {
    id: "self",
    name: "本人",
    active: true,
    department_id: "it",
    is_admin: mode === "all",
  };
  const requests: URLSearchParams[] = [],
    exports: URLSearchParams[] = [],
    errors: string[] = [];
  await page.addInitScript(() =>
    sessionStorage.setItem("token", "isolated-topic-test"),
  );
  page.on("pageerror", (e) => errors.push(e.message));
  await page.route("**/api/**", async (route) => {
    const request = route.request(),
      url = new URL(request.url());
    expect(request.method()).toBe("GET");
    let body: any = [];
    if (url.pathname === "/api/me") body = me;
    else if (url.pathname === "/api/directory")
      body = { users: [me], departments, series: [] };
    else if (url.pathname === "/api/auth/options") body = { wecom: false };
    else if (url.pathname === "/api/reports/access")
      body = {
        mode,
        department_ids: mode === "self" ? [] : ["it", "finance"],
        root_department_ids: ["it", "finance"],
        departments: mode === "self" ? [] : departments,
        users: [me],
        topics:
          mode === "self"
            ? topics.filter((t) => t.department_ids.includes("it"))
            : topics,
      };
    else if (url.pathname === "/api/reports/export") {
      exports.push(url.searchParams);
      return route.fulfill({
        body: "isolated-export",
        contentType:
          "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      });
    } else if (url.pathname === "/api/reports") {
      requests.push(url.searchParams);
      const topic =
        topics.find((t) => t.key === url.searchParams.get("series_key")) ||
        topics[0];
      body = {
        quality: "已确认数据",
        summary: {
          items: 1,
          counts: { 已完成: 1, 未完成: 0, 未到期: 0, 不统计: 0, 关闭: 0 },
          rate: 1,
        },
        groups: [],
        details: [
          {
            id: "row",
            user_id: "self",
            user_name: "本人",
            meeting_id: "meeting",
            meeting_title: topic.name,
            meeting_date: "2026-11-02",
            content: "所选主题的任务",
            status: "已完成",
            due_date: "2026-11-06",
          },
        ],
      };
    }
    await route.fulfill({ json: body });
  });
  await page.goto("/reports?view=regular");
  await expect.poll(() => requests.length).toBe(1);
  return { requests, exports, errors };
}

test("部门联动会议主题，切换部门清空旧主题，导出使用相同筛选", async ({
  page,
}) => {
  const checks = await fixture(page);
  const department = page.getByRole("combobox", {
    name: "筛选部门",
    exact: true,
  });
  await department.fill("信息部");
  await page.getByRole("option", { name: "信息部", exact: true }).click();
  await expect
    .poll(() => checks.requests.at(-1)?.get("department_id"))
    .toBe("it");
  const topic = page.getByRole("combobox", {
    name: "筛选会议主题",
    exact: true,
  });
  await topic.click();
  await expect(
    page.getByRole("option", { name: "信息部工作周会", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("option", { name: "跨部门项目会", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("option", { name: "财务工作周会", exact: true }),
  ).toHaveCount(0);
  await page.getByRole("option", { name: "跨部门项目会", exact: true }).click();
  await expect
    .poll(() => checks.requests.at(-1)?.get("series_key"))
    .toBe("series:shared");
  expect(checks.requests.at(-1)?.has("kind")).toBe(false);
  await page
    .getByRole("button", { name: "查看全部任务明细", exact: true })
    .click();
  await expect(page.locator("#report-details")).toContainText("跨部门项目会");
  await page.getByRole("button", { name: "关闭明细", exact: true }).click();
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "导出 Excel", exact: true }).click();
  await download;
  expect(checks.exports[0].get("series_key")).toBe("series:shared");
  expect(checks.exports[0].get("department_id")).toBe("it");
  await page.screenshot({
    path: path.resolve(
      ".validation/department-series-topics-20261102/reports-desktop.png",
    ),
    fullPage: true,
  });
  await department.fill("财务部");
  await page.getByRole("option", { name: "财务部", exact: true }).click();
  await expect
    .poll(() => checks.requests.at(-1)?.get("department_id"))
    .toBe("finance");
  expect(checks.requests.at(-1)?.get("series_key")).toBeNull();
  await topic.click();
  await expect(
    page.getByRole("option", { name: "信息部工作周会", exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("option", { name: "财务工作周会", exact: true }),
  ).toBeVisible();
  expect(checks.errors).toEqual([]);
});

test("普通员工主题筛选只使用本人统计，点击后弹窗查看明细", async ({ page }) => {
  const checks = await fixture(page, "self");
  await expect(page.locator("#report-details")).toHaveCount(0);
  await expect(
    page.getByRole("heading", { name: "我的已评定任务情况", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("combobox", { name: "筛选部门", exact: true }),
  ).toHaveCount(0);
  const topic = page.getByRole("combobox", {
    name: "筛选会议主题",
    exact: true,
  });
  await topic.fill("跨部门");
  await page.getByRole("option", { name: "跨部门项目会", exact: true }).click();
  await expect
    .poll(() => checks.requests.at(-1)?.get("series_key"))
    .toBe("series:shared");
  expect(checks.requests.at(-1)?.get("person_id")).toBe("self");
  await page
    .getByRole("button", { name: "本人：全部 1 项", exact: true })
    .click();
  await expect(page.locator("#report-details")).toContainText("跨部门项目会");
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({
    path: path.resolve(
      ".validation/department-series-topics-20261102/reports-mobile.png",
    ),
    fullPage: true,
  });
  expect(checks.errors).toEqual([]);
});
