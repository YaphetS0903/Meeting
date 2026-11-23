import { expect, test, type Page } from "@playwright/test";
import { fileURLToPath } from "node:url";

const evidence = fileURLToPath(
  new URL("../../../.impeccable/review/", import.meta.url),
);
async function mock(page: Page) {
  const calls: URL[] = [];
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.addInitScript(() =>
    sessionStorage.setItem("token", "isolated-series-filter"),
  );
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const user = {
      id: "admin",
      name: "系统管理员",
      active: true,
      is_admin: true,
      can_oversee_tasks: true,
    };
    const users = [
      user,
      { id: "person", name: "金丽君", active: true, department_id: "dep" },
    ];
    const departments = [{ id: "dep", name: "综合部" }];
    let body: any = [];
    if (url.pathname === "/api/me") body = user;
    else if (url.pathname === "/api/directory")
      body = { users, departments, series: [] };
    else if (url.pathname === "/api/auth/options") body = { wecom: false };
    else if (url.pathname === "/api/task-overview/options")
      body = {
        users,
        departments,
        series: [
          { id: "management", name: "管理层周例会" },
          { id: "factory", name: "厂长周会" },
        ],
      };
    else if (url.pathname === "/api/task-overview") {
      calls.push(url);
      const selected = url.searchParams.get("series_id");
      const total = selected === "factory" ? 0 : selected ? 24 : 27;
      const offset = Number(url.searchParams.get("offset") || 0);
      body = {
        total,
        summary: { total },
        items: Array.from(
          { length: Math.max(0, Math.min(20, total - offset)) },
          (_, index) => ({
            id: String(index + offset),
            content: "核对工作计划与执行结果",
            department_names: ["综合部"],
            due_date: "2026-11-20",
            owner_names: ["金丽君"],
            verifier_name: "孙玉冰",
            status: "未到期验证",
            pending: false,
            source_title: "管理层周例会",
            source_meeting_date: "2026-11-16",
          }),
        ),
      };
    }
    await route.fulfill({ json: body });
  });
  return { calls, errors };
}

for (const width of [2216, 1366, 390]) {
  test(`会议系列筛选、分页、组合条件和清空 ${width}`, async ({ page }) => {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 900 });
    const result = await mock(page);
    await page.goto("/task-overview?source=meeting");
    await page.getByRole("button", { name: /更多筛选/ }).click();
    const series = page.getByRole("combobox", {
      name: "筛选会议系列",
      exact: true,
    });
    await series.fill("管理层");
    await expect(
      page.getByRole("option", { name: "厂长周会", exact: true }),
    ).toBeHidden();
    await page
      .getByRole("option", { name: "管理层周例会", exact: true })
      .click();
    await expect
      .poll(() => result.calls.at(-1)?.searchParams.get("series_id"))
      .toBe("management");
    await expect(page.locator(".overview-filter-summary")).toContainText(
      "会议系列：管理层周例会",
    );
    await expect(page.locator(".overview-stats")).toContainText("24");
    await expect(page.locator(".overview-results .count")).toHaveText("24");
    await page.getByRole("button", { name: "下一页", exact: true }).click();
    await expect
      .poll(() => result.calls.at(-1)?.searchParams.get("offset"))
      .toBe("20");
    expect(result.calls.at(-1)?.searchParams.get("series_id")).toBe(
      "management",
    );
    await page
      .getByRole("combobox", { name: "筛选部门（包含子部门）" })
      .click();
    await page.getByRole("option", { name: "综合部", exact: true }).click();
    await expect
      .poll(() => result.calls.at(-1)?.searchParams.get("offset"))
      .toBe("0");
    expect(result.calls.at(-1)?.searchParams.get("department_id")).toBe("dep");
    expect(result.calls.at(-1)?.searchParams.get("series_id")).toBe(
      "management",
    );
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    await page.screenshot({
      path: `${evidence}/${width === 2216 ? "user-2216" : width === 1366 ? "desktop" : "mobile"}.png`,
      fullPage: true,
      animations: "disabled",
    });
    await page.getByRole("button", { name: "清空筛选", exact: true }).click();
    await expect
      .poll(() => result.calls.at(-1)?.searchParams.has("series_id"))
      .toBe(false);
    await expect(page.locator(".overview-filter-summary")).toHaveCount(0);
    expect(result.errors).toEqual([]);
  });
}

test("切换来源清除系列，选系列自动限定会议任务，空结果仍可清除", async ({
  page,
}) => {
  const result = await mock(page);
  await page.goto("/task-overview?source=direct");
  await page.getByRole("button", { name: /更多筛选/ }).click();
  await page
    .getByRole("combobox", { name: "筛选会议系列", exact: true })
    .click();
  await page.getByRole("option", { name: "管理层周例会", exact: true }).click();
  await expect
    .poll(() => result.calls.at(-1)?.searchParams.get("source"))
    .toBe("meeting");
  await page
    .getByRole("combobox", { name: "筛选任务来源", exact: true })
    .press("ArrowDown");
  await page.getByRole("option", { name: "直接派发", exact: true }).click();
  await expect
    .poll(() => result.calls.at(-1)?.searchParams.has("series_id"))
    .toBe(false);
  await page
    .getByRole("combobox", { name: "筛选会议系列", exact: true })
    .click();
  await page.getByRole("option", { name: "厂长周会", exact: true }).click();
  await expect(page.locator(".overview-results .count")).toHaveText("0");
  await expect(page.locator(".overview-desktop")).toContainText(
    "当前筛选范围没有任务",
  );
  await page.getByRole("button", { name: "清空筛选", exact: true }).click();
  await expect(page.locator(".overview-results .count")).toHaveText("27");
  expect(result.errors).toEqual([]);
});
