import { expect, test } from "@playwright/test";

test("历史会议可按标题和日期查找，并能从上传记录定位到提取结果", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByLabel("账号", { exact: true }).fill("browser-test");
  await page.getByLabel("密码", { exact: true }).fill("local-test-only-123");
  await page.getByRole("button", { name: "进入工作台" }).click();

  await expect(
    page.getByRole("button", { name: "浏览器验收例会" }),
  ).toBeVisible();
  await expect(page.getByText("待整理的历史会议")).toHaveCount(0);
  await page.setViewportSize({ width: 375, height: 812 });
  await expect(page.getByRole("button", { name: /筛选会议/ })).toBeVisible();
  await expect
    .poll(() => page.evaluate(() => document.documentElement.scrollWidth))
    .toBeLessThanOrEqual(375);
  await page.screenshot({
    path: "test-results/meeting-series-mobile.png",
    fullPage: true,
    animations: "disabled",
  });
  await page.setViewportSize({ width: 1280, height: 720 });
  await page.getByRole("button", { name: "浏览器验收例会" }).click();
  await expect(
    page.getByRole("link", { name: "历史关联基准会议" }),
  ).toBeVisible();
  await page.screenshot({
    path: "test-results/meeting-series-detail.png",
    animations: "disabled",
  });
  await page.getByRole("link", { name: "历史关联基准会议" }).click();
  await expect(
    page.getByRole("heading", { name: "会议纪要", exact: true }),
  ).toBeVisible();
  await page.getByRole("link", { name: "浏览器验收例会" }).click();
  await expect(page.getByRole("button", { name: "会议系列" })).toBeVisible();
  await page.getByRole("button", { name: "会议系列" }).click();
  await page.getByRole("link", { name: "导入历史会议", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "导入历史会议" }),
  ).toBeVisible();
  await expect(page.locator(".recent-import-row")).toHaveCount(2);

  const search = page.getByRole("textbox", {
    name: "搜索历史会议标题或文件名",
  });
  await search.fill("不存在的会议标题");
  await page.getByRole("button", { name: "搜索", exact: true }).click();
  await expect(page.getByText(/没有找到符合条件的会议/)).toBeVisible();

  await search.fill("fixture.xlsx");
  await page.getByRole("button", { name: "搜索", exact: true }).click();
  await expect(page.locator("#import-meetings .el-table__body tr")).toHaveCount(
    1,
  );
  await expect(
    page.locator("#import-meetings .el-table__body tr"),
  ).toContainText("fixture.xlsx");

  const dates = page.locator(".filters .el-range-input");
  await dates.nth(0).fill("2026-09-01");
  await dates.nth(1).fill("2026-09-30");
  await dates.nth(1).press("Enter");
  await expect(page.getByText(/没有找到符合条件的会议/)).toBeVisible();

  await page.getByRole("button", { name: "＋ 导入 Excel 文件" }).click();
  const dialog = page.getByRole("dialog", { name: "导入历史会议 Excel" });
  await expect(dialog.getByText(/不需要先指定月份/)).toBeVisible();
  await dialog
    .locator('input[type="file"]')
    .setInputFiles("/tmp/meeting-actions-e2e-storage/fixture.xlsx");
  await expect(dialog).toBeHidden();
  await expect(page.locator(".recent-import-row")).toHaveCount(3);
  await expect(page.locator(".recent-import-row").first()).toContainText(
    "重复上传，未新增会议",
  );
  await page
    .locator(".recent-import-row")
    .first()
    .getByRole("button", { name: "查看提取的会议" })
    .click();
  await expect(page.locator("#import-meetings .el-table__body tr")).toHaveCount(
    1,
  );

  await page.setViewportSize({ width: 375, height: 812 });
  await expect
    .poll(() => page.evaluate(() => document.documentElement.scrollWidth))
    .toBeLessThanOrEqual(375);
  await page.screenshot({
    path: "test-results/history-import-mobile.png",
    fullPage: true,
    animations: "disabled",
  });
});
