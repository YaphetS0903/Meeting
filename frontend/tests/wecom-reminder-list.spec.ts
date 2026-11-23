import { expect, test } from "@playwright/test";
import { fileURLToPath } from "node:url";

const evidence = fileURLToPath(
  new URL("../../../.validation/wecom-clarity-20261112/", import.meta.url),
);

for (const width of [1366, 390]) {
  test(`个人提醒卡片跳转、任务详情与全部待办（${width}）`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    const me = { id: "owner", name: "责任人甲", active: true, is_admin: false };
    const task = {
      id: "due-task",
      content: "OA验收流程开发",
      version: 1,
      due_date: "2026-11-13",
      source_meeting_id: "meeting",
      source_meeting_title: "系统相关优化方案讨论周工作会议",
      inbox_meeting_title: "系统相关优化方案讨论周工作会议",
      inbox_meeting_date: "2026-11-11",
      owner_ids: [me.id],
      owner_names: [me.name],
      verifier_name: "主持人",
      status: "未到期",
      requests: [],
      versions: [],
      can_edit: false,
      can_view_source: false,
    };
    const queries: URL[] = [],
      errors: string[] = [];
    page.on("pageerror", (e) => errors.push(e.message));
    await page.addInitScript(() =>
      sessionStorage.setItem("token", "isolated-card"),
    );
    await page.route("**/api/**", async (route) => {
      const url = new URL(route.request().url());
      expect(route.request().method()).toBe("GET");
      let body: any = [];
      if (url.pathname === "/api/me") body = me;
      else if (url.pathname === "/api/auth/options") body = { wecom: false };
      else if (url.pathname === "/api/directory")
        body = { users: [me], departments: [], series: [] };
      else if (url.pathname === "/api/tasks") {
        queries.push(url);
        body = { total: 1, items: [task] };
      } else if (url.pathname === "/api/tasks/due-task") body = task;
      else if (url.pathname.endsWith("/native-todo")) body = { enabled: false };
      await route.fulfill({ json: body });
    });
    await page.goto("/tasks?reminder_day=2026-11-12");
    await expect(
      page.getByRole("heading", { name: "2026-11-12 到期提醒" }),
    ).toBeVisible();
    await expect.poll(() => queries.length).toBeGreaterThan(0);
    expect(queries[0].searchParams.get("reminder_day")).toBe("2026-11-12");
    expect(queries[0].searchParams.get("mine")).toBe("true");
    expect(queries[0].searchParams.get("current")).toBe("false");
    await expect(page.getByRole("switch", { name: "只看我的" })).toHaveCount(0);
    const list =
      width < 600
        ? page.locator(".task-mobile-list")
        : page.locator(".task-table");
    await expect(list).toContainText(task.content);
    await expect(list).toContainText(task.inbox_meeting_title);
    await expect(list).toContainText(task.due_date);
    await page.screenshot({
      path: evidence + `reminder-list-${width}.png`,
      fullPage: true,
      animations: "disabled",
    });
    await list.getByRole("button", { name: task.content, exact: true }).click();
    await expect(page.getByRole("dialog")).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.getByRole("dialog")).not.toBeVisible();
    await page.getByRole("button", { name: "全部待办", exact: true }).click();
    await expect(page).toHaveURL(/\/tasks$/);
    await expect(
      page.getByRole("heading", { name: "我的待办", exact: true }),
    ).toBeVisible();
    await expect
      .poll(() => queries.at(-1)?.searchParams.get("current"))
      .toBe("true");
    expect(queries.at(-1)?.searchParams.has("reminder_day")).toBe(false);
    expect(errors).toEqual([]);
  });
}

test("提醒任务已完成时显示空列表，仍可返回全部待办", async ({ page }) => {
  await page.addInitScript(() =>
    sessionStorage.setItem("token", "isolated-empty-card"),
  );
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const body =
      path === "/api/me"
        ? { id: "owner", name: "责任人甲", active: true }
        : path === "/api/directory"
          ? { users: [], departments: [], series: [] }
          : path === "/api/tasks"
            ? { total: 0, items: [] }
            : {};
    await route.fulfill({ json: body });
  });
  await page.goto("/tasks?reminder_day=2026-11-12");
  await expect(
    page.getByRole("heading", { name: "2026-11-12 到期提醒" }),
  ).toBeVisible();
  await expect(page.getByText("共 0 条任务", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "全部待办" })).toBeEnabled();
});
