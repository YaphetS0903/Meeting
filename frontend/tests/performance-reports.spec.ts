import { expect, test, type Page } from "@playwright/test";
async function fixture(
  page: Page,
  self = false,
  failure = false,
  path = "/reports",
) {
  const users = [
    { id: "a", name: "王晨宁", active: true, department_id: "dept" },
    { id: "b", name: "赵彩霞", active: true, department_id: "other" },
    { id: "c", name: "李明", active: true, department_id: "other" },
    { id: "bot", name: "it机器人", active: true, department_id: "dept" },
  ];
  const departments = [
    { id: "dept", name: "技术部" },
    { id: "other", name: "财务部" },
  ];
  const metrics = [
    {
      items: 7,
      assessed: 4,
      completed: 3,
      incomplete: 1,
      not_due: 1,
      excluded: 1,
      closed: 1,
      rate: 0.75,
    },
    {
      items: 6,
      assessed: 5,
      completed: 4,
      incomplete: 1,
      not_due: 1,
      excluded: 0,
      closed: 0,
      rate: 0.8,
    },
    {
      items: 1,
      assessed: 0,
      completed: 0,
      incomplete: 0,
      not_due: 1,
      excluded: 0,
      closed: 0,
      rate: null,
    },
  ];
  const groups = users.slice(0, self ? 1 : 3).map((u, index) => ({
    ...u,
    label: u.name,
    regular: { items: 1, incomplete: index === 0 ? 1 : 0 },
    direct: { items: 1, not_due: 1 },
    total: metrics[index]!,
  }));
  const counts = { 已完成: 3, 未完成: 1, 未到期: 2, 不统计: 0, 关闭: 0 };
  const requests: URL[] = [],
    detailRequests: URL[] = [],
    errors: string[] = [];
  await page.addInitScript(() =>
    sessionStorage.setItem("token", "isolated-performance"),
  );
  page.on("pageerror", (e) => errors.push(e.message));
  await page.route("**/api/**", async (route) => {
    const request = route.request(),
      url = new URL(request.url());
    expect(request.method()).toBe("GET");
    if (url.pathname === "/api/reports/performance/details")
      detailRequests.push(url);
    let body: any = [];
    if (url.pathname === "/api/me") body = { ...users[0], is_admin: !self };
    else if (url.pathname === "/api/directory")
      body = { users, departments, series: [] };
    else if (url.pathname === "/api/meeting-series")
      body = { items: [], total: 0, meeting_total: 0 };
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
          ["total"].map((source) => {
            const values = filtered.map((g) => g.total);
            const combined = Object.fromEntries(
              (
                [
                  "items",
                  "assessed",
                  "completed",
                  "incomplete",
                  "not_due",
                  "excluded",
                  "closed",
                ] as const
              ).map((key) => [
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
    } else if (
      [
        "/api/reports",
        "/api/reports/direct",
        "/api/reports/performance/details",
      ].includes(url.pathname)
    ) {
      if (
        url.pathname.endsWith("/details") ||
        url.searchParams.get("details_only")
      )
        body = {
          details_total: 1,
          details: [
            {
              id: "detail",
              meeting_title: "管理层周例会",
              meeting_date: "2026-11-09",
              content:
                url.searchParams.get("status") === "未到期"
                  ? "准备下周会议资料"
                  : url.searchParams.get("status") === "未完成"
                    ? "完善采购验收材料"
                    : "核对生产计划",
              due_date: "2026-11-12",
              status: url.searchParams.get("status") || "已完成",
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
  await page.goto(path);
  return {
    requests,
    detailRequests,
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
    const results = page.locator(".performance-results");
    await expect(results.getByText("任务合计", { exact: true })).toHaveCount(0);
    await expect(
      results
        .locator(".performance-period")
        .getByRole("button", { name: "导出 Excel" }),
    ).toBeVisible();
    for (const column of [
      "任务总数",
      "已完成",
      "未完成",
      "未到期",
      "不统计",
      "已关闭",
      "绩效达成率",
    ])
      await expect(
        results.getByRole("columnheader", { name: column, exact: true }),
      ).toHaveCount(1);
    for (const source of ["会议纪要统计", "直接派发任务", "考核数"])
      await expect(
        results.getByRole("columnheader", { name: source, exact: true }),
      ).toHaveCount(0);
    await expect(results.getByText("75.0%", { exact: true })).toHaveClass(
      "report-rate-low",
    );
    await expect(results.getByText("80.0%", { exact: true })).not.toHaveClass(
      /report-rate-low/,
    );
    await expect(
      results
        .getByRole("row")
        .filter({ hasText: "李明" })
        .getByText("—", { exact: true }),
    ).not.toHaveClass(/report-rate-low/);
    await expect(
      results.locator(".el-table__footer-wrapper .report-rate-low"),
    ).toHaveText("77.8%");
    const overview = results.locator(".performance-overview");
    await expect(
      overview.getByRole("heading", { name: "任务状态分布" }),
    ).toBeVisible();
    await expect(overview.locator(".report-kpi-rate strong")).toHaveText(
      "77.8%",
    );
    await expect(overview.getByText("已完成 7", { exact: true })).toBeVisible();
    await expect(overview.getByText("未到期 3", { exact: true })).toBeVisible();
    const input = page.getByRole("combobox", {
      name: "绩效统计人员",
      exact: true,
    });
    await input.fill("赵彩");
    await page.getByRole("option", { name: "赵彩霞", exact: true }).click();
    await expect(overview.locator(".report-kpi-rate strong")).toHaveText(
      "80.0%",
    );
    await expect(overview.getByText("已完成 4", { exact: true })).toBeVisible();
    await expect
      .poll(() => checks.requests.at(-1)?.searchParams.get("person_id"))
      .toBe("b");
    await expect(
      page.locator(".performance-results .el-table__body-wrapper tr"),
    ).toHaveCount(1);
    await expect(
      results.getByText("合计（按人）", { exact: true }),
    ).toHaveCount(0);
    await page
      .getByRole("button", { name: "查看赵彩霞的任务明细", exact: true })
      .click();
    const dialog = page.getByRole("dialog");
    await expect(
      dialog.getByText("管理层周例会", { exact: true }),
    ).toBeVisible();
    await dialog
      .locator(".el-radio-button")
      .filter({ hasText: "直接派发任务" })
      .click();
    await expect(
      dialog.getByText("核对生产计划", { exact: true }),
    ).toBeVisible();
    await expect(dialog.getByText("管理层周例会", { exact: true })).toHaveCount(
      0,
    );
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    await dialog.getByRole("button", { name: "关闭", exact: true }).click();
    const download = page.waitForEvent("download");
    await page.getByRole("button", { name: "导出 Excel", exact: true }).click();
    expect((await download).suggestedFilename()).toBe("绩效统计.xlsx");
    expect(checks.errors).toEqual([]);
  });
}

test("绩效统计失败可重试，切换模块后不残留表格", async ({ page }) => {
  const checks = await fixture(page, false, true);
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
  const main = page.getByRole("main");
  const overview = main.locator(".performance-overview");
  await expect(
    overview.getByRole("heading", { name: "任务状态分布" }),
  ).toBeVisible();
  await expect(overview.locator(".report-kpi-rate")).toHaveClass(
    /rate-below-target/,
  );
  await expect(
    overview.getByRole("button", { name: "查看全部绩效任务明细" }),
  ).toHaveText("7");
  await expect(overview.getByText("已完成 3", { exact: true })).toBeVisible();
  await expect(overview.getByText("已关闭 1", { exact: true })).toBeVisible();
  await expect(
    main.getByRole("table").last().getByText("75.0%", { exact: true }),
  ).toBeVisible();
  await expect(main.getByText(/仅本人|考核数|合计（按人）/)).toHaveCount(0);
  await expect(
    main
      .getByRole("row")
      .filter({ hasText: "王晨宁" })
      .getByRole("cell", { name: "王晨宁", exact: true }),
  ).toBeVisible();
  await overview.getByRole("button", { name: "查看全部绩效任务明细" }).click();
  await expect(
    page.getByRole("dialog", { name: "任务明细", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("radio", { name: "中高层会议统计", exact: true }),
  ).toHaveCount(0);
  expect(checks.errors).toEqual([]);
});

test("点击绩效状态数量按人员和状态查看明细", async ({ page }) => {
  const checks = await fixture(page, true);
  const row = page.getByRole("row").filter({ hasText: "王晨宁" });
  const filters = () => {
    const request = checks.detailRequests.at(-1)!;
    return {
      person: request.searchParams.get("person_id"),
      status: request.searchParams.get("status"),
      source: request.searchParams.get("source"),
      offset: request.searchParams.get("offset"),
    };
  };

  await row.getByRole("button", { name: "查看未完成任务明细" }).click();
  let dialog = page.getByRole("dialog", { name: "未完成任务明细" });
  await expect(dialog.getByText("完善采购验收材料")).toBeVisible();
  expect(filters()).toEqual({
    person: "a",
    status: "未完成",
    source: "regular",
    offset: "0",
  });
  await dialog
    .locator(".el-radio-button")
    .filter({ hasText: "直接派发任务" })
    .click();
  await expect
    .poll(filters)
    .toEqual({ person: "a", status: "未完成", source: "direct", offset: "0" });
  await dialog.getByRole("button", { name: "关闭", exact: true }).click();

  await row.getByRole("button", { name: "查看未到期任务明细" }).click();
  dialog = page.getByRole("dialog", { name: "未到期任务明细" });
  await expect(dialog.getByText("准备下周会议资料")).toBeVisible();
  expect(filters()).toEqual({
    person: "a",
    status: "未到期",
    source: "direct",
    offset: "0",
  });
  await dialog.getByRole("button", { name: "关闭", exact: true }).click();

  await row.getByRole("button", { name: "查看任务明细", exact: true }).click();
  await expect(
    page
      .getByRole("dialog", { name: "任务明细", exact: true })
      .getByText("核对生产计划"),
  ).toBeVisible();
  expect(filters()).toEqual({
    person: "a",
    status: null,
    source: "regular",
    offset: "0",
  });
  expect(checks.errors).toEqual([]);
});

for (const self of [false, true]) {
  test(`${self ? "员工" : "系统管理员"}每次从侧栏进入都打开绩效统计`, async ({
    page,
  }) => {
    const checks = await fixture(page, self, false, "/meetings");
    const sidebar = page.locator("#workspace-navigation");
    const reports = sidebar.getByRole("link", {
      name: "统计报表",
      exact: true,
    });
    await reports.click();
    await expect(
      page.getByRole("radio", { name: "绩效统计", exact: true }),
    ).toBeChecked();
    await module(page, "会议纪要统计");
    await expect(
      page.getByRole("radio", { name: "会议纪要统计", exact: true }),
    ).toBeChecked();
    await reports.click();
    await expect(
      page.getByRole("radio", { name: "绩效统计", exact: true }),
    ).toBeChecked();
    await module(page, "会议纪要统计");
    await expect(
      page.getByRole("radio", { name: "会议纪要统计", exact: true }),
    ).toBeChecked();
    await sidebar
      .getByRole("link", { name: "会议纪要与任务单", exact: true })
      .click();
    await reports.click();
    await expect(
      page.getByRole("heading", { name: "人员绩效考核" }),
    ).toBeVisible();
    expect(checks.errors).toEqual([]);
  });
}
