import { test, expect } from "@playwright/test";

test("会议列表请求出错时显示错误和重试，不显示空归档", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("账号", { exact: true }).fill("browser-test");
  await page.getByLabel("密码", { exact: true }).fill("local-test-only-123");
  await page.route(
    (url) => url.pathname === "/api/meeting-series",
    (route) =>
      route.fulfill({
        status: 500,
        contentType: "application/json",
        body: '{"detail":"服务暂时不可用"}',
      }),
  );
  await page.getByRole("button", { name: "进入工作台" }).click();
  await expect(
    page.getByText("会议列表加载失败：服务暂时不可用"),
  ).toBeVisible();
  await expect(
    page.locator(".series-archive-table .el-table__empty-text"),
  ).toHaveText("加载失败，请重试");
  await page.unrouteAll();
  await page.getByRole("button", { name: "重试加载" }).click();
  await expect(page.getByText("会议列表加载失败：服务暂时不可用")).toHaveCount(
    0,
  );
  await expect(
    page.getByRole("button", { name: "浏览器验收例会", exact: true }),
  ).toBeVisible();
});
