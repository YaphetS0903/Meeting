import { test, expect, type Page } from "@playwright/test";
import { fileURLToPath } from "node:url";

async function fixture(page: Page, admin = true, exportAllowed = true) {
  const me = {
    id: "self",
    name: "袁可炜",
    department_id: "it",
    active: true,
    is_admin: admin,
  };
  const other = {
    id: "other",
    name: "王晨宁",
    department_id: "it",
    active: true,
  };
  const departments = [{ id: "it", name: "信息部", parent_id: null }];
  const requests: URLSearchParams[] = [],
    errors: string[] = [];
  let failDetails = false;
  const rows = Array.from({ length: 12 }, (_, i) => ({
    id: `task-${i}`,
    content: `第${i + 1}项：完善生产数据采集流程及系统接口，提交部门核对`,
    issuer_name: "赵彩霞",
    owner_names: [me.name],
    due_date: "2026-11-12",
    date_history: i === 0 ? ["2026-11-05"] : [],
    status: ["已完成", "待验证", "未完成", "未到期"][i % 4],
    requirements: "核对数据口径后提交",
    deliverable: "数据核对表",
  }));
  const summary = {
    items: 12,
    counts: { 已完成: 3, 待验证: 3, 未完成: 3, 未到期: 3 },
    rate: 1 / 3,
    due_completed: 3,
    due_items: 9,
  };
  await page.addInitScript(() =>
    sessionStorage.setItem("token", "isolated-direct-reports"),
  );
  page.on("pageerror", (e) => errors.push(e.message));
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    expect(route.request().method()).toBe("GET");
    let body: any = [];
    if (url.pathname === "/api/me") body = me;
    else if (url.pathname === "/api/directory")
      body = { users: [me, other], departments, series: [] };
    else if (url.pathname === "/api/auth/options") body = { wecom: false };
    else if (url.pathname === "/api/reports/access")
      body = {
        mode: admin ? "all" : "self",
        can_export: exportAllowed,
        can_view_senior_meetings: admin,
        departments: admin ? departments : [],
        department_ids: admin ? ["it"] : [],
        root_department_ids: [],
        users: admin ? [me, other] : [me],
        topics: [],
      };
    else if (url.pathname === "/api/reports")
      body = {
        summary: {
          ...summary,
          counts: { ...summary.counts, 不统计: 0, 关闭: 0 },
        },
        groups: [],
        details: [],
      };
    else if (url.pathname === "/api/reports/direct/export")
      return route.fulfill({
        body: "isolated xlsx",
        contentType:
          "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      });
    else if (url.pathname === "/api/reports/direct") {
      requests.push(url.searchParams);
      if (!admin) {
        expect(url.searchParams.get("person_id")).toBe("self");
        expect(url.searchParams.has("department_id")).toBe(false);
      }
      if (url.searchParams.get("details_only")) {
        if (failDetails)
          return route.fulfill({
            status: 503,
            json: { detail: "明细暂不可用" },
          });
        const matching = rows.filter(
          (r) =>
            !url.searchParams.get("status") ||
            r.status === url.searchParams.get("status"),
        );
        const offset = Number(url.searchParams.get("offset") || 0);
        body = {
          details: matching.slice(offset, offset + 10),
          details_total: matching.length,
        };
      } else
        body = {
          summary,
          groups: [{ id: me.id, label: me.name, active: true, ...summary }],
          undated_count: 0,
        };
    }
    return route.fulfill({ json: body });
  });
  await page.goto("/reports?view=direct");
  await expect(
    page.getByRole("heading", {
      name: admin ? "人员派发任务统计" : "我的派发任务统计",
    }),
  ).toBeVisible();
  return {
    requests,
    errors,
    fail: (value: boolean) => {
      failDetails = value;
    },
  };
}

for (const width of [1366, 390]) {
  test(`派发任务汇总、明细分页和布局 ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    const checks = await fixture(page);
    await expect(page.getByText("计划完成日期", { exact: true })).toBeVisible();
    await expect(page.getByText("会议主题", { exact: true })).toHaveCount(0);
    for (const name of [
      "姓名",
      "任务总数",
      "已完成",
      "待验证",
      "未完成",
      "未到期",
      "到期完成率",
    ])
      await expect(
        page.getByRole("columnheader", { name, exact: true }),
      ).toHaveCount(1);
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    ).toBe(true);
    await page.screenshot({
      path: fileURLToPath(
        new URL(
          `../../../.validation/direct-reports-20261112/${width}-summary.png`,
          import.meta.url,
        ),
      ),
      fullPage: true,
    });
    await page
      .getByRole("button", { name: "查看全部派发任务", exact: true })
      .click();
    const dialog = page.getByRole("dialog", {
      name: "全部派发任务",
      exact: true,
    });
    await expect(dialog).toBeVisible();
    await expect(dialog).toContainText("第1项：完善生产数据");
    await expect(dialog).toContainText("2026-11-05");
    await dialog.getByRole("button", { name: "下一页" }).click();
    await expect(dialog).toContainText("第11项：完善生产数据");
    await expect(dialog).not.toContainText("第1项：完善生产数据");
    await page.screenshot({
      path: fileURLToPath(
        new URL(
          `../../../.validation/direct-reports-20261112/${width}-details.png`,
          import.meta.url,
        ),
      ),
      fullPage: true,
    });
    await dialog.getByRole("button", { name: "关闭明细", exact: true }).click();
    await page
      .getByRole("button", { name: "袁可炜：待验证 3 项", exact: true })
      .click();
    await expect(page.getByRole("dialog")).toHaveAccessibleName(
      "袁可炜 · 待验证派发任务",
    );
    await expect(
      page.getByRole("dialog").getByRole("row").filter({ hasText: "待验证" }),
    ).toHaveCount(3);
    expect(checks.requests.at(-1)?.get("status")).toBe("待验证");
    expect(checks.requests.at(-1)?.get("person_id")).toBe("self");
    expect(checks.errors).toEqual([]);
  });
}

test("普通员工只显示本人，切换回会议报表，导出保留筛选", async ({ page }) => {
  const checks = await fixture(page, false);
  await expect(
    page.getByRole("combobox", { name: "派发统计部门" }),
  ).toHaveCount(0);
  await expect(page.getByText("袁可炜（仅本人）")).toBeVisible();
  const waiting = page.waitForRequest((r) =>
    r.url().includes("/reports/direct/export?"),
  );
  await page.getByRole("button", { name: "导出 Excel", exact: true }).click();
  const exported = new URL((await waiting).url());
  expect(exported.searchParams.get("person_id")).toBe("self");
  expect(exported.searchParams.has("start")).toBe(true);
  await page
    .locator(".el-radio-button")
    .filter({ hasText: "会议纪要统计" })
    .click();
  await expect(page.getByText("我的已评定任务情况")).toBeVisible();
  await expect(page.getByText("我的派发任务统计")).toHaveCount(0);
  expect(checks.errors).toEqual([]);
});

test("明细加载失败可重试，无导出权时按钮禁用", async ({ page }) => {
  const checks = await fixture(page, true, false);
  await expect(page.getByRole("button", { name: "导出 Excel" })).toBeDisabled();
  checks.fail(true);
  await page
    .getByRole("button", { name: "查看全部派发任务", exact: true })
    .click();
  await expect(page.getByRole("dialog")).toContainText("明细暂不可用");
  checks.fail(false);
  await page.getByRole("button", { name: "重试读取明细", exact: true }).click();
  await expect(page.getByRole("dialog")).toContainText("第1项：完善生产数据");
});
