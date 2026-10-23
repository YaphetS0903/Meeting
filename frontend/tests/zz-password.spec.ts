import { test, expect } from "@playwright/test";

test("管理员可修改密码，旧会话失效后可在当前页面继续使用", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("账号", { exact: true }).fill("browser-test");
  await page.getByLabel("密码", { exact: true }).fill("local-test-only-123");
  await page.getByRole("button", { name: "进入工作台" }).click();
  await expect(page.getByRole("button", { name: "修改密码" })).toBeVisible();
  const oldToken = await page.evaluate(() => sessionStorage.getItem("token"));
  await page.setViewportSize({ width: 375, height: 812 });
  await page.getByRole("button", { name: "修改密码" }).click();
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth),
  ).toBeLessThanOrEqual(375);
  const dialog = page.getByRole("dialog", { name: "修改密码" });
  await dialog.getByLabel("当前密码").fill("local-test-only-123");
  await dialog
    .getByLabel("新密码（至少 15 个字符）")
    .fill("temporary-browser-password-2026");
  await dialog
    .getByLabel("再次输入新密码")
    .fill("temporary-browser-password-2026");
  await dialog.getByRole("button", { name: "确认修改" }).click();
  await expect(dialog).not.toBeVisible();
  const previous = await page.evaluate(
    async (token) =>
      (
        await fetch("/api/me", {
          headers: { Authorization: `Bearer ${token}` },
        })
      ).status,
    oldToken,
  );
  expect(previous).toBe(401);
  await page.reload();
  await expect(page.getByRole("button", { name: "修改密码" })).toBeVisible();
  // Restore the isolated browser fixture account for other runs.
  await page.getByRole("button", { name: "修改密码" }).click();
  const restore = page.getByRole("dialog", { name: "修改密码" });
  await restore.getByLabel("当前密码").fill("temporary-browser-password-2026");
  await restore
    .getByLabel("新密码（至少 15 个字符）")
    .fill("local-test-only-123");
  await restore.getByLabel("再次输入新密码").fill("local-test-only-123");
  await restore.getByRole("button", { name: "确认修改" }).click();
  await expect(restore).not.toBeVisible();
});
