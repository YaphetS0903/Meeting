import { test, expect, type Page } from "@playwright/test";
const employee = {
  id: "audit-person",
  name: "林明",
  active: true,
  is_admin: false,
  department_id: "audit-dep",
};
async function base(page: Page, handler: (url: URL) => any) {
  await page.addInitScript(() =>
    sessionStorage.setItem("token", "isolated-audit-test"),
  );
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const body =
      url.pathname === "/api/me"
        ? employee
        : url.pathname === "/api/directory"
          ? {
              users: [employee],
              departments: [{ id: "audit-dep", name: "信息部" }],
              series: [],
            }
          : url.pathname === "/api/auth/options"
            ? { wecom: false }
            : handler(url);
    if (body === undefined)
      throw new Error(`Unexpected request ${url.pathname}`);
    await route.fulfill({ json: body });
  });
}
test("报表汇总不预装明细，弹窗按本人和结果分页读取，读取失败可重试", async ({
  page,
}) => {
  const reads: URL[] = [];
  let fail = false;
  await base(page, (url) => {
    if (url.pathname === "/api/reports/access")
      return { mode: "self", users: [employee], departments: [], topics: [] };
    if (url.pathname === "/api/reports") {
      reads.push(url);
      if (url.searchParams.get("details_only") === "true")
        return {
          details_total: 27,
          details: Array.from(
            {
              length: Math.min(10, 27 - Number(url.searchParams.get("offset"))),
            },
            (_, i) => ({
              id: "detail-" + (i + Number(url.searchParams.get("offset"))),
              user_id: employee.id,
              user_name: employee.name,
              meeting_title: "信息部例会",
              meeting_date: "2026-11-05",
              content: "完成资料核对第" + (i + 1),
              status: "已完成",
              due_date: "2026-11-10",
            }),
          ),
        };
      return {
        details_paginated: true,
        details: [],
        groups: [],
        summary: {
          items: 27,
          counts: { 已完成: 27, 未完成: 0, 未到期: 0, 不统计: 0, 关闭: 0 },
          rate: 1,
        },
      };
    }
  });
  await page.goto("/reports?view=regular");
  await expect(
    page.getByRole("heading", { name: "我的已评定任务情况", exact: true }),
  ).toBeVisible();
  await expect(page.getByText("林明（仅本人）", { exact: true })).toBeVisible();
  expect(
    reads.every((u) => u.searchParams.get("include_details") === "false"),
  ).toBeTruthy();
  await page
    .getByRole("button", { name: "林明：已完成 27 项", exact: true })
    .click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByText(/共 27 项/)).toBeVisible();
  await dialog.locator(".el-pagination .btn-next").click();
  await expect.poll(() => reads.at(-1)?.searchParams.get("offset")).toBe("10");
  expect(reads.at(-1)?.searchParams.get("person_id")).toBe(employee.id);
  expect(reads.at(-1)?.searchParams.get("status")).toBe("已完成");
  await page.route("**/api/reports?**", async (route) => {
    const url = new URL(route.request().url());
    if (url.searchParams.get("details_only") === "true" && fail)
      return route.fulfill({ status: 503, json: { detail: "明细暂时不可用" } });
    await route.fallback();
  });
  fail = true;
  await dialog.locator(".el-pagination .btn-next").click();
  await expect(
    dialog.getByText("明细暂时不可用", { exact: true }),
  ).toBeVisible();
  fail = false;
  await dialog
    .getByRole("button", { name: "重试读取明细", exact: true })
    .click();
  await expect(dialog.getByText("明细暂时不可用", { exact: true })).toHaveCount(
    0,
  );
});
test("我的待办在服务器分页，查询会回到第一页", async ({ page }) => {
  const reads: URL[] = [];
  await base(page, (url) => {
    if (url.pathname === "/api/tasks") {
      reads.push(url);
      return {
        total: 45,
        items: Array.from({ length: 20 }, (_, index) => ({
          id: "task-" + (index + Number(url.searchParams.get("offset"))),
          content: "核对资料" + index,
          owner_names: [employee.name],
          verifier_name: employee.name,
          todo_status: "未到期验证",
          source_meeting_id: "meeting",
          inbox_meeting_date: "2026-11-05",
          inbox_meeting_title: "信息部例会",
        })),
      };
    }
  });
  await page.goto("/tasks");
  await expect(page.getByText("共 45 条任务", { exact: true })).toBeVisible();
  await page.locator(".el-pagination .btn-next").click();
  await expect.poll(() => reads.at(-1)?.searchParams.get("offset")).toBe("20");
  await page
    .getByRole("textbox", { name: "搜索任务", exact: true })
    .fill("资料");
  await page.getByRole("button", { name: "查询", exact: true }).click();
  await expect.poll(() => reads.at(-1)?.searchParams.get("offset")).toBe("0");
  expect(reads.at(-1)?.searchParams.get("q")).toBe("资料");
});
