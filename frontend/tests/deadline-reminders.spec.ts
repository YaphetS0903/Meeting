import { expect, test } from "@playwright/test";
import { fileURLToPath } from "node:url";

const evidence = fileURLToPath(new URL("../../../.validation/deadline-reminders-20261104/", import.meta.url));
for (const width of [1366, 390]) {
  test(`截止提醒开关独立保存，旧每日汇总保持关闭（${width}）`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    const me = { id: "admin", name: "系统管理员", is_admin: true, active: true };
    let config = { enabled: true, deadline_enabled: true, daily_enabled: false, hour: 9,
      before_days: 1, overdue_enabled: true, verification_hours: 24 };
    const writes: any[] = [], errors: string[] = [];
    page.on("pageerror", e => errors.push(e.message));
    await page.addInitScript(() => sessionStorage.setItem("token", "isolated-deadline"));
    await page.route("**/api/**", async route => {
      const url = new URL(route.request().url()), path = url.pathname;
      let body: any = [];
      if (route.request().method() !== "GET") {
        expect(path).toBe("/api/admin/reminders");
        config = route.request().postDataJSON();
        writes.push(config);
        return route.fulfill({ json: { ...config, cancelled_events: 0 } });
      }
      if (path === "/api/me") body = me;
      else if (path === "/api/directory") body = { users: [me], departments: [], series: [] };
      else if (path === "/api/auth/options") body = { wecom: false };
      else if (path === "/api/admin/reminders") body = config;
      else if (path === "/api/admin/config") body = { wecom_configured: false };
      else if (path === "/api/admin/native-todo") body = { allowed_users: [] };
      else if (["/api/admin/operations", "/api/admin/integration-checks"].includes(path)) body = {};
      await route.fulfill({ json: body });
    });
    await page.goto("/settings");
    const panel = page.locator("section.panel").filter({ has: page.getByRole("heading", { name: "提醒规则", exact: true }) });
    const switches = panel.getByRole("switch");
    await expect(switches.nth(1)).toBeChecked();
    await expect(switches.nth(2)).not.toBeChecked();
    await expect(panel).toContainText("上午9点按规则提醒");
    await expect(panel).toContainText("三天及以上到期：前两天提醒一次");
    await panel.screenshot({ path: evidence + `settings-${width}.png`, animations: "disabled" });
    await switches.nth(1).locator("..").click();
    await panel.getByRole("button", { name: "保存提醒规则" }).click();
    await expect.poll(() => writes.length).toBe(1);
    expect(writes[0].deadline_enabled).toBe(false);
    expect(writes[0].daily_enabled).toBe(false);
    await switches.nth(1).locator("..").click();
    await panel.getByRole("button", { name: "保存提醒规则" }).click();
    await expect.poll(() => writes.length).toBe(2);
    expect(writes[1].deadline_enabled).toBe(true);
    expect(writes[1].daily_enabled).toBe(false);
    expect(errors).toEqual([]);
  });
}
