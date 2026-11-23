import { expect, test, type Page } from "@playwright/test";
import { fileURLToPath } from "node:url";

const statusNames = ["已完成", "未完成", "未到期", "不统计", "关闭"];
async function fixture(page: Page, admin = false, large = false) {
  const me = {
    id: "self",
    name: "袁可炜",
    department_id: "it",
    active: true,
    is_admin: admin,
  };
  const other = {
    id: "other",
    name: "另一位员工",
    department_id: "it",
    active: true,
  };
  const departments = [{ id: "it", name: "信息部", parent_id: null }];
  const requests: URLSearchParams[] = [],
    errors: string[] = [];
  const counts = large ? [20, 13, 20, 2, 2] : [1, 0, 5, 0, 0];
  const mine = counts.flatMap((count, index) =>
    Array.from({ length: count }, (_, number) => ({
      id: `${index}-${number}`,
      user_id: "self",
      user_name: me.name,
      meeting_id: "it-weekly",
      meeting_title: "系统相关优化方案讨论周工作会议",
      meeting_date: "2026-11-02",
      content: `${statusNames[index]}：工作事项 ${number + 1}`,
      due_date: "2026-11-06",
      status: statusNames[index],
    })),
  );
  const summary = (rows: any[]) => {
    const result = Object.fromEntries(
      statusNames.map((status) => [
        status,
        rows.filter((row) => row.status === status).length,
      ]),
    );
    return {
      items: rows.length,
      counts: result,
      rate:
        result["已完成"]! + result["未完成"]!
          ? result["已完成"]! / (result["已完成"]! + result["未完成"]!)
          : null,
    };
  };
  await page.addInitScript(() =>
    sessionStorage.setItem("token", "isolated-report-dialog"),
  );
  page.on("pageerror", (error) => errors.push(error.message));
  await page.route("**/api/**", async (route) => {
    const request = route.request(),
      url = new URL(request.url());
    expect(request.method()).toBe("GET");
    let body: any = [];
    if (url.pathname === "/api/me") body = me;
    else if (url.pathname === "/api/directory")
      body = { users: [me, other], departments, series: [] };
    else if (url.pathname === "/api/auth/options") body = { wecom: false };
    else if (url.pathname === "/api/reports/access")
      body = {
        mode: admin ? "all" : "self",
        departments: admin ? departments : [],
        department_ids: admin ? ["it"] : [],
        root_department_ids: [],
        users: admin ? [me, other] : [me],
        topics: [
          { key: "series:it", name: "信息部周会", department_ids: ["it"] },
        ],
      };
    else if (url.pathname === "/api/reports") {
      requests.push(url.searchParams);
      if (!admin) {
        expect(url.searchParams.get("person_id")).toBe("self");
        expect(url.searchParams.has("department_id")).toBe(false);
      }
      const foreign = admin
        ? [
            {
              ...mine[0],
              id: "foreign",
              user_id: "other",
              user_name: other.name,
              content: "另一人的工作事项",
            },
          ]
        : [];
      const details = [...mine, ...foreign];
      body = {
        quality: "已确认数据",
        summary: summary(details),
        details,
        groups: [
          { id: "self", label: me.name, active: true, ...summary(mine) },
          ...(admin
            ? [
                {
                  id: "other",
                  label: other.name,
                  active: true,
                  ...summary(foreign),
                },
              ]
            : []),
        ],
      };
    }
    return route.fulfill({ json: body });
  });
  await page.goto("/reports");
  await expect(
    page.locator(admin ? ".stat-grid" : "#department-people-results"),
  ).toBeVisible();
  return { requests, errors };
}
async function capture(page: Page, name: string) {
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({
    path: fileURLToPath(
      new URL(
        `../../../.impeccable/review/report-details-dialog/${name}.png`,
        import.meta.url,
      ),
    ),
    fullPage: !name.endsWith("-dialog"),
    animations: "disabled",
  });
}

for (const width of [1440, 1366, 390]) {
  test(`员工汇总和明细弹窗适配 ${width}px，空状态可关闭并恢复焦点`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 960 });
    const checks = await fixture(page);
    await expect(
      page.getByRole("heading", { name: "我的已评定任务情况", exact: true }),
    ).toBeVisible();
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await expect(page.locator("main #report-details")).toHaveCount(0);
    const register = page.locator("#department-people-results");
    await expect(register.getByRole("row")).toHaveCount(2);
    for (const name of ["事项数", ...statusNames, "达成率"])
      await expect(
        register.getByRole("columnheader", { name, exact: true }),
      ).toHaveCount(1);
    await expect(register).not.toContainText("另一位员工");
    await expect(page.locator(".stat-grid")).toHaveCount(0);
    await expect(
      page.getByRole("combobox", { name: "筛选部门", exact: true }),
    ).toHaveCount(0);
    await expect(
      page.getByRole("combobox", { name: "筛选人员", exact: true }),
    ).toHaveCount(0);
    const label =
      width === 1440 ? "desktop" : width === 1366 ? "user-1366" : "mobile";
    await capture(page, label);
    const completed = page.getByRole("button", {
      name: "袁可炜：已完成 1 项",
      exact: true,
    });
    await completed.click();
    const dialog = page.getByRole("dialog", {
      name: "袁可炜 · 已完成任务明细",
      exact: true,
    });
    await expect(dialog).toBeVisible();
    await expect(
      dialog.getByRole("row").filter({ hasText: "已完成：工作事项 1" }),
    ).toHaveCount(1);
    await expect(dialog).not.toContainText("未到期：工作事项");
    await expect(
      dialog.getByRole("link", { name: /系统相关优化/ }),
    ).toHaveCount(0);
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBeTruthy();
    await capture(page, `${label}-dialog`);
    await page.keyboard.press("Escape");
    await expect(dialog).not.toBeVisible();
    await expect(completed).toBeFocused();
    await page
      .getByRole("button", { name: "袁可炜：未完成 0 项", exact: true })
      .click();
    await expect(page.getByRole("dialog")).toContainText("暂无对应明细");
    await page.getByRole("button", { name: "关闭明细", exact: true }).click();
    await page
      .getByRole("button", { name: "袁可炜：未到期 5 项", exact: true })
      .click();
    await expect(
      page
        .getByRole("dialog")
        .getByRole("row")
        .filter({ hasText: "未到期：工作事项" }),
    ).toHaveCount(5);
    expect(checks.errors).toEqual([]);
    expect(checks.requests).toHaveLength(1);
  });
}

test("数据较多时在弹窗中分页，切换状态或周期重置明细", async ({ page }) => {
  const checks = await fixture(page, false, true);
  await page
    .getByRole("button", { name: "袁可炜：全部 57 项", exact: true })
    .click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.locator(".el-table__body-wrapper tbody tr")).toHaveCount(
    10,
  );
  await dialog.locator("button.btn-next").click();
  await expect(dialog).toContainText("已完成：工作事项 11");
  await expect(
    dialog.getByRole("cell", { name: "已完成：工作事项 1", exact: true }),
  ).toHaveCount(0);
  await dialog.locator(".el-pagination__sizes .el-select").click();
  await page.getByRole("option", { name: /20/ }).click();
  await expect(dialog.locator(".el-table__body-wrapper tbody tr")).toHaveCount(
    20,
  );
  await expect(dialog.locator(".el-pager .is-active")).toHaveText("1");
  await page.getByRole("button", { name: "关闭明细", exact: true }).click();
  await page
    .getByRole("button", { name: "袁可炜：不统计 2 项", exact: true })
    .click();
  await expect(dialog.locator(".el-table__body-wrapper tbody tr")).toHaveCount(
    2,
  );
  await expect(dialog).toHaveAttribute("aria-label", "袁可炜 · 不统计任务明细");
  await page.getByRole("button", { name: "关闭明细", exact: true }).click();
  await expect(dialog).not.toBeVisible();
  await page.locator(".el-radio-button").filter({ hasText: "按周" }).click();
  await expect.poll(() => checks.requests.length).toBe(2);
  await expect(dialog).not.toBeVisible();
  expect(checks.errors).toEqual([]);
});

test("员工点击未完成数字只显示本人未完成事项", async ({ page }) => {
  const checks = await fixture(page, false, true);
  await page
    .getByRole("button", { name: "袁可炜：未完成 13 项", exact: true })
    .click();
  const dialog = page.getByRole("dialog", {
    name: "袁可炜 · 未完成任务明细",
    exact: true,
  });
  await expect(dialog).toBeVisible();
  await expect(dialog.locator(".el-table__body-wrapper tbody tr")).toHaveCount(
    10,
  );
  await expect(dialog).not.toContainText("已完成：工作事项");
  await expect(dialog).not.toContainText("另一位员工");
  await dialog.locator("button.btn-next").click();
  await expect(dialog.locator(".el-table__body-wrapper tbody tr")).toHaveCount(
    3,
  );
  await expect(dialog).toContainText("未完成：工作事项 13");
  await expect(page.locator("main #report-details")).toHaveCount(0);
  expect(checks.errors).toEqual([]);
});

test("部门报表按人员和状态打开弹窗，顶部汇总打开全部对应任务", async ({
  page,
}) => {
  const checks = await fixture(page, true);
  await page
    .getByRole("combobox", { name: "筛选部门", exact: true })
    .fill("信息部");
  await page.getByRole("option", { name: "信息部", exact: true }).click();
  await page
    .getByRole("button", { name: "另一位员工：已完成 1 项", exact: true })
    .click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toContainText("另一人的工作事项");
  await expect(dialog).not.toContainText("已完成：工作事项 1");
  await expect(
    dialog.getByRole("link", { name: /系统相关优化/ }),
  ).toHaveAttribute("href", "/meetings/it-weekly");
  await page.getByRole("button", { name: "关闭明细", exact: true }).click();
  await page
    .getByRole("button", { name: "查看已完成任务明细", exact: true })
    .click();
  await expect(dialog.locator(".el-table__body-wrapper tbody tr")).toHaveCount(
    2,
  );
  await expect(page.locator("main #report-details")).toHaveCount(0);
  expect(checks.errors).toEqual([]);
});
