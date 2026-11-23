import { expect, test, type Page } from "@playwright/test";
import { fileURLToPath } from "node:url";

const evidence = fileURLToPath(
  new URL("../../../.validation/report-navigation-20261118/", import.meta.url),
);
async function fixture(page: Page, self = false) {
  const users = [
    { id: "a", name: "王晨宁", active: true, department_id: "dept" },
    { id: "b", name: "赵彩霞", active: true, department_id: "other" },
    { id: "bot", name: "it机器人", active: true, department_id: "dept" },
  ];
  const departments = [
    { id: "dept", name: "技术部" },
    { id: "other", name: "财务部" },
  ];
  const metric = {
    items: 6,
    assessed: 4,
    completed: 3,
    incomplete: 1,
    rate: 0.75,
  };
  const groups = users.slice(0, self ? 1 : 2).map((u) => ({
    ...u,
    label: u.name,
    regular: metric,
    direct: metric,
    total: { items: 12, assessed: 8, completed: 6, incomplete: 2, rate: 0.75 },
  }));
  const counts = { 已完成: 3, 未完成: 1, 未到期: 2, 不统计: 0, 关闭: 0 };
  const requests: URL[] = [],
    errors: string[] = [];
  let failure = false;
  await page.addInitScript(() =>
    sessionStorage.setItem("token", "isolated-performance"),
  );
  page.on("pageerror", (e) => errors.push(e.message));
  await page.route("**/api/**", async (route) => {
    const request = route.request(),
      url = new URL(request.url());
    expect(request.method()).toBe("GET");
    let body: any = [];
    if (url.pathname === "/api/me") body = { ...users[0], is_admin: !self };
    else if (url.pathname === "/api/directory")
      body = { users, departments, series: [] };
    else if (url.pathname === "/api/auth/options") body = { wecom: false };
    else if (url.pathname === "/api/reports/access")
      body = {
        mode: self ? "self" : "all",
        can_export: true,
        can_view_senior_meetings: !self,
        users: self ? users.slice(0, 1) : users,
        departments: self ? [] : departments,
        department_ids: departments.map((d) => d.id),
        topics: [],
      };
    else if (url.pathname === "/api/reports/performance/export")
      return route.fulfill({
        body: "isolated workbook",
        contentType:
          "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      });
    else if (url.pathname === "/api/reports/performance") {
      requests.push(url);
      if (failure)
        return route.fulfill({
          status: 503,
          json: { detail: "统计服务暂不可用" },
        });
      const filtered = groups.filter(
        (g) =>
          (!url.searchParams.get("person_id") ||
            g.id === url.searchParams.get("person_id")) &&
          (!url.searchParams.get("department_id") ||
            g.department_id === url.searchParams.get("department_id")),
      );
      body = {
        groups: filtered,
        summary: Object.fromEntries(
          ["regular", "direct", "total"].map((source) => {
            const values = filtered.map((g: any) => g[source]);
            const combined = Object.fromEntries(
              ["items", "assessed", "completed", "incomplete"].map((key) => [
                key,
                values.reduce((sum, row) => sum + row[key], 0),
              ]),
            );
            return [
              source,
              {
                ...combined,
                rate: combined.assessed
                  ? combined.completed / combined.assessed
                  : null,
              },
            ];
          }),
        ),
        undated_count: 0,
        as_of: "2026-11-13",
      };
    } else if (["/api/reports", "/api/reports/direct"].includes(url.pathname)) {
      if (url.searchParams.get("details_only"))
        body = {
          details_total: 1,
          details: [
            {
              id: "detail",
              meeting_title: "管理层周例会",
              meeting_date: "2026-11-09",
              content: "核对生产计划",
              due_date: "2026-11-12",
              status: "已完成",
            },
          ],
        };
      else
        body = {
          summary: {
            items: 6,
            counts: { ...counts, 待验证: 0 },
            rate: 0.75,
            due_items: 4,
            due_completed: 3,
          },
          groups: groups.map((g) => ({
            ...g,
            items: 6,
            counts,
            rate: 0.75,
            meetings: [],
          })),
          details: [],
          undated_count: 0,
        };
    }
    return route.fulfill({ json: body });
  });
  await page.goto("/reports");
  await expect(
    page.getByRole("radio", { name: "会议纪要统计", exact: true }),
  ).toBeChecked();
  return {
    requests,
    errors,
    fail: (value: boolean) => {
      failure = value;
    },
  };
}
async function module(page: Page, name: string) {
  await page
    .locator(".report-module-picker .el-radio-button")
    .filter({ hasText: name })
    .click();
}

for (const width of [1366, 390]) {
  test(`按需搜人、模块顺序、绩效表与来源明细 ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    const checks = await fixture(page);
    await expect(
      page.locator(".report-module-picker .el-radio-button"),
    ).toHaveText(["绩效统计", "会议纪要统计", "直接派发任务统计"]);
    for (const [name, label] of [
      ["会议纪要统计", "筛选人员"],
      ["直接派发任务统计", "派发统计人员"],
      ["绩效统计", "绩效统计人员"],
    ]) {
      await module(page, name!);
      const input = page.getByRole("combobox", { name: label!, exact: true });
      await input.click();
      await expect(
        page.getByRole("option", { name: "王晨宁", exact: true }),
      ).toHaveCount(0);
      await input.fill("it机器人");
      await expect(
        page.getByRole("option", { name: "it机器人", exact: true }),
      ).toHaveCount(0);
      await input.fill("王晨");
      await expect(
        page.getByRole("option", { name: "王晨宁", exact: true }),
      ).toBeVisible();
      await input.press("Escape");
      await page
        .getByRole("heading", { name: "统计报表", exact: true })
        .click();
    }
    await expect(
      page.getByRole("heading", { name: "人员绩效考核" }),
    ).toBeVisible();
    await expect(
      page
        .locator(".performance-results .el-table__body-wrapper")
        .getByText("it机器人", { exact: true }),
    ).toHaveCount(0);
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    await page.screenshot({
      path: `${evidence}/performance-${width}.png`,
      fullPage: true,
    });
    const input = page.getByRole("combobox", {
      name: "绩效统计人员",
      exact: true,
    });
    await input.fill("赵彩");
    await page.getByRole("option", { name: "赵彩霞", exact: true }).click();
    await expect
      .poll(() => checks.requests.at(-1)?.searchParams.get("person_id"))
      .toBe("b");
    await expect(
      page.locator(".performance-results .el-table__body-wrapper tr"),
    ).toHaveCount(1);
    for (const source of ["会议纪要统计", "直接派发任务"]) {
      await page
        .getByRole("button", { name: `查看赵彩霞的${source}任务`, exact: true })
        .click();
      const dialog = page.getByRole("dialog");
      await expect(
        dialog.getByText("核对生产计划", { exact: true }),
      ).toBeVisible();
      if (source === "会议纪要统计")
        await expect(
          dialog.getByText("管理层周例会", { exact: true }),
        ).toBeVisible();
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
      ).toBe(true);
      await dialog.getByRole("button", { name: "关闭", exact: true }).click();
    }
    const download = page.waitForEvent("download");
    await page.getByRole("button", { name: "导出 Excel", exact: true }).click();
    expect((await download).suggestedFilename()).toBe("绩效统计.xlsx");
    expect(checks.errors).toEqual([]);
  });
}

test("绩效统计失败可重试，切换模块后不残留表格", async ({ page }) => {
  const checks = await fixture(page);
  checks.fail(true);
  await module(page, "绩效统计");
  await expect(
    page.getByText("统计服务暂不可用", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "导出 Excel", exact: true }),
  ).toBeDisabled();
  checks.fail(false);
  await page.getByRole("button", { name: "重试加载", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "人员绩效考核" }),
  ).toBeVisible();
  await module(page, "直接派发任务统计");
  await expect(page.locator(".performance-results")).toHaveCount(0);
  await expect(
    page.getByRole("heading", { name: "人员派发任务统计" }),
  ).toBeVisible();
  expect(checks.errors).toEqual([]);
});

test("普通人员绩效表仅显示本人", async ({ page }) => {
  const checks = await fixture(page, true);
  await module(page, "绩效统计");
  await expect(
    page.getByRole("heading", { name: "人员绩效考核" }),
  ).toBeVisible();
  await expect(
    page.getByRole("combobox", { name: "绩效统计人员" }),
  ).toHaveCount(0);
  await expect
    .poll(() => checks.requests.at(-1)?.searchParams.get("person_id"))
    .toBe("a");
  expect(checks.requests.at(-1)?.searchParams.has("department_id")).toBe(false);
  await expect(
    page.locator(".performance-results .el-table__body-wrapper tr"),
  ).toHaveCount(1);
  await expect(
    page.getByRole("radio", { name: "中高层会议统计", exact: true }),
  ).toHaveCount(0);
  expect(checks.errors).toEqual([]);
});
