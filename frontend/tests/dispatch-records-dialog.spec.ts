import { expect, test, type Page } from "@playwright/test";
import { fileURLToPath } from "node:url";

const evidence = fileURLToPath(
  new URL("../../../.validation/task-labels-20261106/", import.meta.url),
);
const me = {
  id: "issuer",
  name: "袁可炜",
  active: true,
  is_admin: true,
  can_assign_tasks: true,
  department_id: "it",
};
function tasks(count = 45) {
  return Array.from({ length: count }, (_, index) => ({
    id: `task-${index + 1}`,
    content: `采购清单核对 ${String(index + 1).padStart(2, "0")}`,
    code: `RW-${String(index + 1).padStart(4, "0")}`,
    owner_names: [index === 42 ? "李四" : "张三"],
    due_date: "2026-11-20" as string | null,
    status: index === 0 ? "关闭" : "未到期验证",
    closed_at: index === 0 ? "2026-11-05T09:00:00" : null,
    withdrawn_at: index === 1 ? "2026-11-05T09:00:00" : null,
  }));
}
async function setup(page: Page, count = 45, canAssign = true) {
  const controller = {
    rows: tasks(count),
    fail: false,
    listReads: 0,
    errors: [] as string[],
    writes: [] as string[],
  };
  page.on("pageerror", (error) => controller.errors.push(error.message));
  await page.addInitScript(() =>
    sessionStorage.setItem("token", "isolated-dispatch-records"),
  );
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (request.method() !== "GET") {
      controller.writes.push(`${request.method()} ${path}`);
      throw new Error("Issued records must not write business data");
    }
    let body: unknown;
    if (path === "/api/me") body = me;
    else if (path === "/api/auth/options") body = { wecom: false };
    else if (path === "/api/directory")
      body = {
        users: [me],
        departments: [{ id: "it", name: "信息部" }],
        series: [],
      };
    else if (path === "/api/direct-assignments/options")
      body = {
        can_assign: canAssign,
        scope: "all",
        users: [me],
        native_todo_auto_confirm: false,
        wecom_bound: false,
        wecom_notifications_enabled: false,
      };
    else if (path === "/api/direct-assignments") {
      controller.listReads++;
      if (controller.fail)
        return route.fulfill({
          status: 503,
          json: { detail: "派发记录暂时不可用" },
        });
      body = controller.rows;
    } else throw new Error(`Unexpected request: ${path}`);
    await route.fulfill({ json: body });
  });
  await page.goto("/assignments");
  await expect(
    page.getByRole("heading", { name: "直接派发任务", exact: true }),
  ).toBeVisible();
  await expect.poll(() => controller.listReads).toBe(1);
  return controller;
}

test("派发记录的人员筛选隐藏机器人，历史记录仍可查看", async ({ page }) => {
  const control = await setup(page);
  control.rows[0].owner_names.push("RPA机器人01");
  await page
    .getByRole("main")
    .getByRole("button", { name: "我派发的任务", exact: true })
    .click();
  const dialog = page.getByRole("dialog", {
    name: "我派发的任务",
    exact: true,
  });
  await dialog.getByRole("button", { name: /^筛选/ }).click();
  const input = dialog.getByRole("combobox", {
    name: "筛选责任人",
    exact: true,
  });
  await input.fill("RPA机器人01");
  await expect(
    page.getByRole("option", { name: "RPA机器人01", exact: true }),
  ).toHaveCount(0);
  await input.fill("张三");
  await expect(
    page.getByRole("option", { name: "张三", exact: true }),
  ).toBeVisible();
  await input.press("Escape");
  await expect(dialog.locator(".el-table__body")).toContainText("RPA机器人01");
});

for (const width of [1366, 820, 390, 320]) {
  test(`sidebar opens issued page while existing entry keeps the dialog at ${width}px`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 900 });
    const controller = await setup(page);
    const input = page.getByRole("textbox", { name: "任务内容", exact: true });
    await input.fill("正在编写、尚未派发的安排");
    await page.evaluate(() => window.scrollTo(0, 0));
    const trigger = page.getByRole("main").getByRole("button", {
      name: "我派发的任务",
      exact: true,
    });
    await expect(trigger).toBeInViewport();
    await expect(page.getByRole("table")).toHaveCount(0);
    if (width > 1440) {
      const preview = await page
        .getByRole("complementary", { name: "派发预览" })
        .boundingBox();
      const entry = await trigger.boundingBox();
      expect(entry!.y).toBeGreaterThan(preview!.y + preview!.height);
      expect(Math.abs(entry!.x - preview!.x)).toBeLessThan(2);
    } else {
      const previewToggle = page.getByRole("button", {
        name: "派发预览",
        exact: true,
      });
      await expect(previewToggle).toBeVisible();
      await previewToggle.click();
      await expect(
        page.getByRole("complementary", { name: "派发预览" }),
      ).toBeVisible();
      await page.getByRole("button", { name: "收起预览", exact: true }).click();
      await expect(
        page.getByRole("complementary", { name: "派发预览" }),
      ).toBeHidden();
    }
    if (width === 1366 || width === 390)
      await page.screenshot({
        path: `${evidence}entry-${width}.png`,
        fullPage: false,
        animations: "disabled",
      });
    await trigger.click();
    const dialog = page.getByRole("dialog", {
      name: "我派发的任务",
      exact: true,
    });
    await expect(dialog).toBeVisible();
    await expect(dialog.locator(".el-table__body")).not.toContainText("RW-");
    await expect(page).toHaveURL(/\/assignments$/);
    await expect(dialog.getByRole("status")).toContainText("共 45 项");
    await expect.poll(() => controller.listReads).toBe(2);
    await expect(
      dialog.getByRole("link", { name: "查看详情", exact: true }),
    ).toHaveCount(20);
    const bounds = await dialog.boundingBox();
    expect(bounds!.x).toBeGreaterThanOrEqual(0);
    expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(width);
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth),
    ).toBeLessThanOrEqual(width);
    if (width === 1366 || width === 390)
      await page.screenshot({
        path: `${evidence}dialog-${width}.png`,
        fullPage: false,
        animations: "disabled",
      });
    await dialog.getByRole("button", { name: "关闭", exact: true }).click();
    await expect(dialog).toBeHidden();
    await expect(input).toHaveValue("正在编写、尚未派发的安排");
    if (width < 768) await page.locator(".navigation-trigger").click();
    const nav = page
      .getByRole("navigation", { name: "主导航" })
      .getByRole("link", { name: "我派发的任务", exact: true });
    await nav.click();
    await expect(page).toHaveURL(/\/my-assignments$/);
    if (width >= 768) await expect(nav).toHaveAttribute("aria-current", "page");
    else await expect(page.locator(".navigation-drawer")).toBeHidden();
    await expect(
      page.getByRole("heading", { name: "我派发的任务", exact: true }),
    ).toBeVisible();
    const records = page.getByRole("region", { name: "派发记录", exact: true });
    await expect(records.getByRole("status")).toContainText("共 45 项");
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth),
    ).toBeLessThanOrEqual(width);
    if (width === 1366 || width === 390)
      await page.screenshot({
        path: `${evidence}page-${width}.png`,
        fullPage: false,
        animations: "disabled",
      });
    expect(controller.errors).toEqual([]);
    expect(controller.writes).toEqual([]);
  });
}

test("all issued records remain reachable through pagination, search and refresh", async ({
  page,
}) => {
  const controller = await setup(page);
  await page
    .getByRole("navigation", { name: "主导航" })
    .getByRole("link", { name: "我派发的任务", exact: true })
    .click();
  const dialog = page.getByRole("region", {
    name: "派发记录",
    exact: true,
  });
  await expect(dialog.getByRole("status")).toContainText(
    "共 45 项 · 43 项正在跟进",
  );
  await expect(dialog.getByText("已撤回", { exact: true })).toBeVisible();
  await expect(
    dialog.getByRole("cell", { name: "关闭", exact: true }),
  ).toBeVisible();
  await dialog.getByRole("button", { name: "下一页", exact: true }).click();
  await expect(
    dialog.getByRole("link", { name: "采购清单核对 21", exact: true }),
  ).toBeVisible();
  await dialog.getByRole("button", { name: "下一页", exact: true }).click();
  await expect(
    dialog.getByRole("link", { name: "查看详情", exact: true }),
  ).toHaveCount(5);
  await expect(
    dialog.getByRole("link", { name: "采购清单核对 45", exact: true }),
  ).toHaveAttribute("href", "/tasks?id=task-45");
  const search = dialog.getByRole("textbox", {
    name: "搜索派发记录",
    exact: true,
  });
  await search.fill("李四");
  await dialog
    .locator(".dispatch-records-search")
    .getByRole("button", { name: "查询", exact: true })
    .click();
  await expect(dialog.getByRole("status")).toContainText("筛选到 1 项");
  await expect(
    dialog.getByRole("link", { name: "采购清单核对 43", exact: true }),
  ).toBeVisible();
  await search.fill("RW-0045");
  await search.press("Enter");
  await expect(
    dialog.getByRole("link", { name: "采购清单核对 45", exact: true }),
  ).toBeVisible();
  await search.fill("不存在的安排");
  await dialog
    .locator(".dispatch-records-search")
    .getByRole("button", { name: "查询", exact: true })
    .click();
  await expect(
    dialog.getByText("没有匹配的任务", { exact: true }),
  ).toBeVisible();
  controller.rows = tasks(1);
  await search.fill("");
  await search.press("Enter");
  await dialog.getByRole("button", { name: "刷新", exact: true }).click();
  await expect(dialog.getByRole("status")).toContainText("共 1 项");
  await expect(
    dialog.getByRole("link", { name: "查看详情", exact: true }),
  ).toHaveCount(1);
  await page.reload();
  await expect(
    page.getByRole("heading", { name: "我派发的任务", exact: true }),
  ).toBeVisible();
  await expect(dialog.getByRole("status")).toContainText("共 1 项");
  expect(controller.errors).toEqual([]);
  expect(controller.writes).toEqual([]);
});

test("empty and failed issued records can be retried from the standalone page", async ({
  page,
}) => {
  const controller = await setup(page, 0);
  await page
    .getByRole("textbox", { name: "任务内容", exact: true })
    .fill("保留这段安排");
  controller.fail = true;
  await page
    .getByRole("navigation", { name: "主导航" })
    .getByRole("link", { name: "我派发的任务", exact: true })
    .click();
  const dialog = page.getByRole("region", {
    name: "派发记录",
    exact: true,
  });
  await expect(dialog.getByRole("alert")).toContainText("派发记录加载失败");
  controller.fail = false;
  await dialog.getByRole("button", { name: "刷新", exact: true }).click();
  await expect(dialog.getByText("暂无派发记录", { exact: true })).toBeVisible();
  await expect(dialog.getByRole("alert")).toHaveCount(0);
  expect(controller.errors).toEqual([]);
  expect(controller.writes).toEqual([]);
});

test("the issued page fetches newly dispatched records and retains only the current user endpoint", async ({
  page,
}) => {
  const controller = await setup(page);
  const latest = {
    ...tasks(1)[0],
    id: "newly-issued",
    code: "RW-0046",
    content: "刚派发的新安排",
    closed_at: null,
    status: "未到期验证",
  };
  controller.rows.unshift(latest);
  await page
    .getByRole("navigation", { name: "主导航" })
    .getByRole("link", { name: "我派发的任务", exact: true })
    .click();
  await expect(
    page.getByRole("region", { name: "派发记录" }).getByRole("status"),
  ).toContainText("共 46 项");
  await expect(
    page.getByRole("link", { name: "刚派发的新安排", exact: true }),
  ).toHaveAttribute("href", "/tasks?id=newly-issued");
  expect(controller.writes).toEqual([]);
  expect(controller.errors).toEqual([]);
});

for (const [mode, width] of [
  ["page", 1366],
  ["dialog", 390],
] as const) {
  test(`query button and expandable combined filters work in ${mode} at ${width}px`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 1000 });
    const controller = await setup(page);
    controller.rows = tasks(7).map((row, index) => ({
      ...row,
      content: index === 5 ? "设备保养" : `设备验收 ${index + 1}`,
      owner_names: index === 4 ? ["张三"] : ["张三", "李四"],
      due_date: [
        "2026-11-06",
        "2026-11-08",
        "2026-11-05",
        "2026-11-09",
        "2026-11-07",
        "2026-11-07",
        null,
      ][index],
    }));
    if (mode === "page") {
      await page
        .getByRole("navigation", { name: "主导航" })
        .getByRole("link", { name: "我派发的任务", exact: true })
        .click();
    } else {
      await page
        .getByRole("main")
        .getByRole("button", { name: "我派发的任务", exact: true })
        .click();
    }
    const records = page.getByRole("region", { name: "派发记录", exact: true });
    const filters = records.getByRole("form", { name: "派发记录筛选条件" });
    const toggle = records.getByRole("button", { name: /^筛选/ });
    await expect(records.getByRole("status")).toContainText("共 7 项");
    await expect(filters).toBeHidden();
    await expect(toggle).toHaveAttribute("aria-expanded", "false");
    const search = records.getByRole("textbox", {
      name: "搜索派发记录",
      exact: true,
    });
    await search.fill("验收");
    await expect(
      records.getByRole("link", { name: "查看详情", exact: true }),
    ).toHaveCount(7);
    await records
      .locator(".dispatch-records-search")
      .getByRole("button", { name: "查询", exact: true })
      .click();
    await expect(
      records.getByRole("link", { name: "查看详情", exact: true }),
    ).toHaveCount(6);
    await toggle.click();
    await filters
      .getByRole("textbox", { name: "筛选任务内容", exact: true })
      .fill("设备");
    await filters
      .getByPlaceholder("开始日期", { exact: true })
      .fill("2026-11-06");
    await filters.getByPlaceholder("开始日期", { exact: true }).press("Tab");
    await filters
      .getByPlaceholder("结束日期", { exact: true })
      .fill("2026-11-08");
    await filters.getByPlaceholder("结束日期", { exact: true }).press("Tab");
    await filters
      .getByRole("combobox", { name: "筛选责任人", exact: true })
      .fill("李");
    await page.getByRole("option", { name: "李四", exact: true }).click();
    await filters.getByRole("button", { name: "查询", exact: true }).click();
    await expect(records.getByRole("status")).toContainText("筛选到 2 项");
    await expect(
      records.getByRole("link", { name: "设备验收 1", exact: true }),
    ).toBeVisible();
    await expect(
      records.getByRole("link", { name: "设备验收 2", exact: true }),
    ).toBeVisible();
    await expect(toggle).toContainText("3");
    const recordBounds = (await records.boundingBox())!;
    for (const field of await filters
      .locator(".dispatch-date-range .el-input__wrapper")
      .all()) {
      const bounds = (await field.boundingBox())!;
      expect(bounds.x).toBeGreaterThanOrEqual(recordBounds.x);
      expect(bounds.x + bounds.width).toBeLessThanOrEqual(
        recordBounds.x + recordBounds.width,
      );
    }
    await page.screenshot({
      path: `${evidence}filters-${mode}-${width}.png`,
      fullPage: false,
      animations: "disabled",
    });
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth),
    ).toBeLessThanOrEqual(width);
    await toggle.click();
    await expect(filters).toBeHidden();
    await expect(records.getByRole("status")).toContainText("筛选到 2 项");
    await records.getByRole("button", { name: "刷新", exact: true }).click();
    await expect(records.getByRole("status")).toContainText("筛选到 2 项");
    await toggle.click();
    await filters.getByRole("button", { name: "重置", exact: true }).click();
    await expect(
      records.getByRole("link", { name: "查看详情", exact: true }),
    ).toHaveCount(7);
    await expect(search).toHaveValue("");
    await expect(records.getByRole("status")).not.toContainText("筛选到");
    expect(controller.writes).toEqual([]);
    expect(controller.errors).toEqual([]);
  });
}

test("date boundaries, invalid ranges and empty matches remain usable at 320px", async ({
  page,
}) => {
  await page.setViewportSize({ width: 320, height: 1000 });
  const controller = await setup(page);
  controller.rows = tasks(3).map((row, index) => ({
    ...row,
    due_date: ["2026-11-06", "2026-11-08", null][index],
  }));
  await page.locator(".navigation-trigger").click();
  await page
    .getByRole("navigation", { name: "主导航" })
    .getByRole("link", { name: "我派发的任务", exact: true })
    .click();
  const records = page.getByRole("region", { name: "派发记录", exact: true });
  await expect(records.getByRole("status")).toContainText("共 3 项");
  await records.getByRole("button", { name: /^筛选/ }).click();
  const filters = records.getByRole("form", { name: "派发记录筛选条件" });
  const start = filters.getByPlaceholder("开始日期", { exact: true });
  const end = filters.getByPlaceholder("结束日期", { exact: true });
  await start.fill("2026-11-08");
  await start.press("Tab");
  await filters.getByRole("button", { name: "查询", exact: true }).click();
  await expect(records.getByRole("status")).toContainText("筛选到 1 项");
  await end.fill("2026-11-06");
  await end.press("Tab");
  await filters.getByRole("button", { name: "查询", exact: true }).click();
  await expect(filters.getByRole("alert")).toContainText(
    "开始日期不能晚于结束日期",
  );
  await expect(records.getByRole("status")).toContainText("筛选到 1 项");
  await start.fill("");
  await start.press("Tab");
  await filters.getByRole("button", { name: "查询", exact: true }).click();
  await expect(filters.getByRole("alert")).toHaveCount(0);
  await expect(
    records.getByRole("link", { name: "采购清单核对 01", exact: true }),
  ).toBeVisible();
  await records
    .getByRole("textbox", { name: "搜索派发记录", exact: true })
    .fill("没有的任务");
  await records
    .getByRole("textbox", { name: "搜索派发记录", exact: true })
    .press("Enter");
  await expect(
    records.getByText("没有匹配的任务", { exact: true }),
  ).toBeVisible();
  await page.screenshot({
    path: `${evidence}filters-page-320.png`,
    fullPage: true,
    animations: "disabled",
  });
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth),
  ).toBeLessThanOrEqual(320);
  await filters.getByRole("button", { name: "重置", exact: true }).click();
  await expect(
    records.getByRole("link", { name: "查看详情", exact: true }),
  ).toHaveCount(3);
  expect(controller.writes).toEqual([]);
  expect(controller.errors).toEqual([]);
});
