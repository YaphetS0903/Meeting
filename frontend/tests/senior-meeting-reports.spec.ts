import { expect, test, type Page } from "@playwright/test";

async function fixture(
  page: Page,
  kind: "admin" | "delegate" | "employee" | "department_role" = "admin",
) {
  const canView = ["admin", "delegate"].includes(kind);
  const me = {
    id: "self",
    name: "报表用户",
    active: true,
    is_admin: kind === "admin",
    department_id: "it",
  };
  const departments = [
    { id: "it", name: "信息部", parent_id: null },
    { id: "hr", name: "人力资源部", parent_id: null },
  ];
  const people = [
    { id: "person", name: "张三", active: true, department_id: "it" },
    { id: "foreign", name: "外部门员工", active: true, department_id: "hr" },
  ];
  const requests: URL[] = [],
    errors: string[] = [];
  let fail = false,
    empty = false,
    revoke = false;
  await page.addInitScript(() =>
    sessionStorage.setItem("token", "isolated-senior-reports"),
  );
  page.on("pageerror", (e) => errors.push(e.message));
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    expect(route.request().method()).toBe("GET");
    let body: any = [];
    if (url.pathname === "/api/me") body = me;
    else if (url.pathname === "/api/auth/options") body = { wecom: false };
    else if (url.pathname === "/api/directory")
      body = { users: people, departments, series: [] };
    else if (url.pathname === "/api/reports/access") {
      const senior = url.searchParams.get("senior_only") === "true";
      requests.push(url);
      if (senior && (revoke || !canView))
        return route.fulfill({
          status: 403,
          json: { detail: "中高层会议统计权限已撤销" },
        });
      const scoped = kind === "delegate" && senior;
      body = {
        mode:
          kind === "employee"
            ? "self"
            : kind === "admin"
              ? "all"
              : "departments",
        can_view_senior_meetings: canView && !revoke,
        senior_leaders: [
          { id: "4437f664-9bb9-42b7-aaa7-8047b95b2860", name: "朱慧铃" },
          { id: "ca542db9-8884-4b81-8280-1228e7c85fcb", name: "侯雨茜" },
        ],
        department_ids: scoped ? ["it"] : departments.map((d) => d.id),
        root_department_ids: scoped ? ["it"] : [],
        departments: scoped ? [departments[0]] : departments,
        users: scoped ? [people[0]] : people,
        topics: [
          {
            key: "series:senior",
            name: "中高层经营会议",
            department_ids: ["it"],
          },
          ...(!senior
            ? [
                {
                  key: "series:regular",
                  name: "普通周会",
                  department_ids: ["it", "hr"],
                },
              ]
            : []),
        ],
      };
    } else if (url.pathname === "/api/reports/new-arrangements") {
      requests.push(url);
      if (revoke || !canView)
        return route.fulfill({
          status: 403,
          json: { detail: "中高层会议统计权限已撤销" },
        });
      if (fail)
        return route.fulfill({
          status: 503,
          json: { detail: "新安排暂时读取失败" },
        });
      const offset = Number(url.searchParams.get("offset") || 0);
      const limit = Number(url.searchParams.get("limit") || 20);
      const details = empty
        ? []
        : Array.from({ length: 28 }, (_, i) => ({
            id: `arrangement-${i}`,
            user_name: "张三",
            meeting_id: "meeting",
            meeting_title: "中高层经营会议",
            meeting_date: "2026-11-04",
            content: `本次新任务 ${i + 1}`,
            requirements: "核对整改结果",
            due_date: "2026-11-11",
            deliverable: "整改记录",
            notes: "新安排备注",
          }));
      body = {
        total: details.length,
        meeting_count: empty ? 0 : 1,
        details: details.slice(offset, offset + limit),
        offset,
        limit,
      };
    } else if (
      url.pathname === "/api/reports" ||
      url.pathname === "/api/reports/export"
    ) {
      requests.push(url);
      if (url.pathname.endsWith("export"))
        return route.fulfill({
          body: "isolated-excel",
          contentType:
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        });
      if (fail)
        return route.fulfill({
          status: 503,
          json: { detail: "报表服务暂时不可用" },
        });
      const senior = url.searchParams.get("senior_only") === "true";
      const counts = {
        已完成: empty ? 0 : 1,
        未完成: empty ? 0 : 1,
        未到期: 0,
        不统计: 0,
        关闭: 0,
      };
      const summary = {
        items: empty ? 0 : 2,
        counts,
        rate: empty ? null : 0.5,
      };
      body = {
        quality: "已确认数据",
        senior_only: senior,
        senior_meeting_count: empty ? 0 : 1,
        senior_published_meeting_count: empty ? 0 : 1,
        leader_name: "朱慧铃",
        senior_meetings: empty
          ? []
          : [
              {
                id: "meeting",
                title: "中高层经营会议",
                meeting_date: "2026-11-04",
                leaders: ["朱慧铃"],
              },
            ],
        new_arrangements_count: empty ? 0 : 28,
        summary,
        groups: empty
          ? []
          : [
              {
                id: kind === "employee" ? me.id : "person",
                label: kind === "employee" ? me.name : "张三",
                active: true,
                ...summary,
                meetings: senior
                  ? [
                      {
                        id: "meeting",
                        title: "中高层经营会议",
                        meeting_date: "2026-11-04",
                      },
                      {
                        id: "meeting-2",
                        title: "采购部重点项目进度与跨部门协作专项协调会议",
                        meeting_date: "2026-11-06",
                      },
                    ]
                  : [
                      {
                        id: "regular",
                        title: "普通周会",
                        meeting_date: "2026-11-04",
                      },
                    ],
              },
            ],
        details: empty
          ? []
          : ["已完成", "未完成"].map((status, i) => ({
              id: String(i),
              user_id: "person",
              user_name: "张三",
              meeting_id: "meeting",
              meeting_title: senior ? "中高层经营会议" : "普通周会",
              meeting_date: "2026-11-04",
              content: status + "的工作事项",
              due_date: "2026-11-06",
              status,
            })),
      };
    }
    return route.fulfill({ json: body });
  });
  await page.goto("/reports");
  await expect
    .poll(() => requests.some((r) => r.pathname === "/api/reports"))
    .toBe(true);
  return {
    requests,
    errors,
    setFail: (v: boolean) => {
      fail = v;
    },
    setEmpty: () => {
      empty = true;
    },
    revoke: () => {
      revoke = true;
    },
  };
}

for (const width of [1366, 390]) {
  test(`会议纪要统计全部部门直接显示人员会议与结果 ${width}`, async ({
    page,
  }, testInfo) => {
    await page.setViewportSize({ width, height: 900 });
    const control = await fixture(page);
    const table = page.locator("#department-people-results");
    await expect(
      page.getByRole("heading", { name: "会议纪要统计 · 人员任务完成情况" }),
    ).toBeVisible();
    for (const name of [
      "姓名",
      "会议名称",
      "事项数",
      "已完成",
      "未完成",
      "未到期",
      "不统计",
      "关闭",
      "达成率",
    ])
      await expect(
        table.getByRole("columnheader", { name, exact: true }),
      ).toHaveCount(1);
    const row = table.locator(".el-table__body tr").filter({ hasText: "张三" });
    await expect(row).toContainText("普通周会");
    await expect(row).toContainText("2026-11-04");
    await expect(row).toContainText("50.0%");
    await page.screenshot({
      path: testInfo.outputPath(`regular-${width}.png`),
      fullPage: true,
      animations: "disabled",
    });
    await table
      .getByRole("button", { name: "张三：已完成 1 项", exact: true })
      .click();
    await expect(page.getByRole("dialog")).toContainText("已完成的工作事项");
    await expect(page.getByRole("dialog")).toContainText("普通周会");
    await page.getByRole("dialog").locator(".el-dialog__headerbtn").click();
    await page.getByRole("combobox", { name: "筛选部门", exact: true }).click();
    await page.getByRole("option", { name: "信息部", exact: true }).click();
    await expect(
      page.getByRole("heading", { name: "信息部 · 人员任务完成情况" }),
    ).toBeVisible();
    expect(
      control.requests
        .filter((r) => r.pathname === "/api/reports")
        .at(-1)
        ?.searchParams.get("department_id"),
    ).toBe("it");
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    expect(control.errors).toEqual([]);
  });
}

for (const width of [1440, 1366, 390]) {
  test(`系统管理员中高层统计筛选、明细、导出与恢复 ${width}`, async ({
    page,
  }, testInfo) => {
    await page.setViewportSize({ width, height: 1000 });
    const control = await fixture(page);
    await expect(
      page
        .getByRole("radiogroup", { name: "统计模块", exact: true })
        .getByRole("radio"),
    ).toHaveCount(3);
    await expect(
      page.getByRole("radiogroup", { name: "会议统计范围", exact: true }),
    ).toBeVisible();
    await page.getByText("中高层会议统计", { exact: true }).click();
    await expect
      .poll(() =>
        control.requests
          .filter((r) => r.pathname === "/api/reports")
          .at(-1)
          ?.searchParams.get("senior_only"),
      )
      .toBe("true");
    await expect(
      page.getByText("朱慧铃或侯雨茜任意一人担任主持人或参会人员", {
        exact: false,
      }),
    ).toHaveCount(0);
    await expect(
      page.getByRole("heading", { name: "中高层会议 · 人员任务完成情况" }),
    ).toBeVisible();
    await expect(
      page.getByRole("radio", { name: "会议纪要统计", exact: true }),
    ).toBeChecked();
    await expect(
      page
        .getByRole("radiogroup", { name: "统计模块", exact: true })
        .getByText("中高层会议统计", { exact: true }),
    ).toHaveCount(0);

    const personTable = page.locator("#department-people-results");
    await expect(
      personTable.getByRole("columnheader", { name: "会议名称", exact: true }),
    ).toBeVisible();
    const personRow = personTable
      .locator(".el-table__body tr")
      .filter({ hasText: "张三" });
    await expect(personRow).toContainText("中高层经营会议");
    await expect(personRow).toContainText(
      "采购部重点项目进度与跨部门协作专项协调会议",
    );
    await expect(personRow).toContainText("2026-11-06");
    await expect(page.locator(".report-senior-rule")).toHaveCount(0);
    await expect(page.locator(".report-new-arrangements")).toHaveCount(0);
    await expect(
      page.getByRole("button", { name: "查看筛选后的会议", exact: true }),
    ).toContainText("会议（1）");
    const leader = page.getByRole("combobox", {
      name: "筛选参会领导",
      exact: true,
    });
    await leader.click();
    await expect(page.getByRole("option")).toHaveCount(2);
    await page.getByRole("option", { name: "侯雨茜", exact: true }).click();
    await expect
      .poll(() =>
        control.requests
          .filter((r) => r.pathname === "/api/reports")
          .at(-1)
          ?.searchParams.get("leader_id"),
      )
      .toBe("ca542db9-8884-4b81-8280-1228e7c85fcb");
    await leader.click();
    await page.getByRole("option", { name: "朱慧铃", exact: true }).click();
    await expect
      .poll(() =>
        control.requests
          .filter((r) => r.pathname === "/api/reports")
          .at(-1)
          ?.searchParams.get("leader_id"),
      )
      .toBe("4437f664-9bb9-42b7-aaa7-8047b95b2860");
    await page
      .getByRole("button", { name: "查看筛选后的会议", exact: true })
      .click();
    const meetings = page.getByRole("dialog", {
      name: "筛选后的中高层会议",
      exact: true,
    });
    await expect(meetings).toContainText("中高层经营会议");
    await expect(meetings).toContainText("朱慧铃");
    await meetings.locator(".el-dialog__headerbtn").click();
    const topic = page.getByRole("combobox", {
      name: "筛选会议主题",
      exact: true,
    });
    await topic.click();
    await expect(page.getByRole("option", { name: "普通周会" })).toHaveCount(0);
    await page
      .getByRole("option", { name: "中高层经营会议", exact: true })
      .click();
    await expect
      .poll(() =>
        control.requests
          .filter((r) => r.pathname === "/api/reports")
          .at(-1)
          ?.searchParams.get("series_key"),
      )
      .toBe("series:senior");
    await page.locator("input").first().blur();
    await page.evaluate(() => {
      window.scrollTo(0, 0);
      (document.activeElement as HTMLElement)?.blur();
    });
    await page.screenshot({
      path: testInfo.outputPath(`senior-${width}.png`),
      fullPage: true,
      animations: "disabled",
    });
    await page
      .getByRole("button", { name: "查看本次新安排 28 项", exact: true })
      .click();
    const arranged = page.getByRole("dialog");
    await expect(arranged).toContainText("本次新任务 1");
    expect(
      control.requests
        .filter((r) => r.pathname.endsWith("new-arrangements"))
        .at(-1)
        ?.searchParams.get("leader_id"),
    ).toBe("4437f664-9bb9-42b7-aaa7-8047b95b2860");
    await expect(arranged).toContainText("核对整改结果");
    await expect(arranged).toContainText("整改记录");
    await expect(arranged).not.toContainText("考核结果");
    await arranged.locator(".el-pagination .btn-next").click();
    await expect(arranged).toContainText("本次新任务 11");
    await expect(
      arranged.getByText("本次新任务 1", { exact: true }),
    ).toHaveCount(0);
    await arranged.screenshot({
      path: testInfo.outputPath(`arrangements-${width}.png`),
      animations: "disabled",
    });
    await arranged.getByRole("button", { name: "关闭明细" }).click();
    await page
      .getByRole("button", { name: "查看已完成任务明细", exact: true })
      .click();
    const dialog = page.getByRole("dialog");
    await expect(dialog).toContainText("已完成的工作事项");
    await expect(dialog).not.toContainText("未完成的工作事项");
    await dialog.screenshot({
      path: testInfo.outputPath(`detail-${width}.png`),
      animations: "disabled",
    });
    await dialog.getByRole("button", { name: "关闭明细" }).click();
    control.setFail(true);
    await page
      .getByRole("button", { name: "查看本次新安排 28 项", exact: true })
      .click();
    await expect(page.getByRole("dialog")).toContainText("新安排暂时读取失败");
    control.setFail(false);
    await page.getByRole("button", { name: "重试读取明细" }).click();
    await expect(page.getByRole("dialog")).toContainText("本次新任务 1");
    await page.getByRole("button", { name: "关闭明细" }).click();
    await page.getByRole("button", { name: "导出 Excel" }).click();
    await expect
      .poll(() =>
        control.requests
          .filter((r) => r.pathname.endsWith("export"))
          .at(-1)
          ?.searchParams.get("senior_only"),
      )
      .toBe("true");
    expect(
      control.requests
        .filter((r) => r.pathname.endsWith("export"))
        .at(-1)
        ?.searchParams.get("leader_id"),
    ).toBe("4437f664-9bb9-42b7-aaa7-8047b95b2860");
    control.setFail(true);
    await page
      .getByRole("radiogroup", { name: "会议统计范围", exact: true })
      .getByText("全部会议", { exact: true })
      .click();
    await expect(leader).toHaveCount(0);
    expect(
      control.requests
        .filter((r) => r.pathname === "/api/reports")
        .at(-1)
        ?.searchParams.has("leader_id"),
    ).toBe(false);
    await expect(
      page.getByRole("alert").filter({ hasText: "报表加载失败" }),
    ).toContainText("报表服务暂时不可用");
    control.setFail(false);
    await page.getByRole("button", { name: "重试加载" }).click();
    await expect(
      page.locator("#department-people-results .report-person-meetings"),
    ).toHaveCount(1);
    control.setEmpty();
    await page.getByText("中高层会议统计", { exact: true }).click();
    await expect(
      page.getByText("本期暂无符合条件的已评定事项", { exact: true }),
    ).toBeVisible();
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBeTruthy();
    expect(control.errors).toEqual([]);
  });
}

test("报表管理员的中高层部门选项只包含显式授权，撤销后清空数据", async ({
  page,
}) => {
  const control = await fixture(page, "delegate");
  await page.getByText("中高层会议统计", { exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "中高层会议 · 人员任务完成情况" }),
  ).toBeVisible();
  await page.getByRole("combobox", { name: "筛选部门", exact: true }).click();
  await expect(
    page.getByRole("option", { name: "人力资源部", exact: true }),
  ).toHaveCount(0);
  await page.getByRole("option", { name: "信息部", exact: true }).click();
  await expect
    .poll(() =>
      control.requests
        .filter((r) => r.pathname === "/api/reports")
        .at(-1)
        ?.searchParams.get("department_id"),
    )
    .toBe("it");
  control.revoke();
  await page
    .getByRole("combobox", { name: "筛选会议主题", exact: true })
    .click();
  await page
    .getByRole("option", { name: "中高层经营会议", exact: true })
    .click();
  await expect(
    page.getByRole("alert").filter({ hasText: "报表加载失败" }),
  ).toContainText("权限已撤销");
  await expect(page.locator("#department-people-results")).toHaveCount(0);
  expect(control.errors).toEqual([]);
});

for (const kind of ["employee", "department_role"] as const) {
  test(`${kind}不能看见中高层模块且常规报表保持原样`, async ({ page }) => {
    const control = await fixture(page, kind);
    await expect(
      page.getByRole("radio", { name: "中高层会议统计", exact: true }),
    ).toHaveCount(0);
    expect(
      control.requests.some(
        (r) => r.searchParams.get("senior_only") === "true",
      ),
    ).toBe(false);
    if (kind === "employee")
      await expect(
        page.getByRole("heading", { name: "我的已评定任务情况" }),
      ).toBeVisible();
    await expect(page.locator("#department-people-results")).toContainText(
      "普通周会",
    );
    await expect(page.locator("#department-people-results")).toContainText(
      "2026-11-04",
    );
    if (kind === "employee")
      await expect(
        page.locator("#department-people-results"),
      ).not.toContainText("张三");
    expect(control.errors).toEqual([]);
  });
}
