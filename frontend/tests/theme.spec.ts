import { test, expect } from "@playwright/test";

async function login(page: import("@playwright/test").Page) {
  await page.goto("/");
  await page.getByLabel("账号", { exact: true }).fill("browser-test");
  await page.getByLabel("密码", { exact: true }).fill("local-test-only-123");
  await page.getByRole("button", { name: "进入工作台" }).click();
  await expect(
    page.getByRole("heading", { name: "会议纪要与任务单" }),
  ).toBeVisible();
}

test("统一蓝色主题覆盖主要页面并适配移动端", async ({ page }) => {
  await login(page);

  const panelStyle = await page
    .locator(".panel")
    .first()
    .evaluate((node) => {
      const style = getComputedStyle(node);
      return {
        radius: style.borderRadius,
        background: style.backgroundColor,
      };
    });
  expect(Number.parseFloat(panelStyle.radius)).toBeGreaterThanOrEqual(8);
  expect(panelStyle.background).toMatch(/rgba?\(255, 255, 255/);

  for (const name of ["我的待办", "待我验证", "统计报表"]) {
    await page.getByRole("link", { name, exact: true }).click();
    await expect(page.locator("h1")).toBeVisible();
  }

  await page.getByRole("link", { name: "系统管理", exact: true }).click();
  const notificationSwitch = page
    .locator(".el-form-item")
    .filter({ hasText: "会议提醒及纪要邮件开关" })
    .locator(".el-switch");
  await expect(notificationSwitch).toBeVisible();
  await notificationSwitch.click();
  await page.getByRole("button", { name: "保存提醒规则" }).click();
  await expect(page.getByText(/会议提醒及纪要邮件已关闭/)).toBeVisible();
  await notificationSwitch.click();
  await page.getByRole("button", { name: "保存提醒规则" }).click();
  await expect(page.getByText(/会议发布通知已开启/)).toBeVisible();

  await page.getByRole("link", { name: "会议纪要与任务单" }).click();
  await page
    .getByRole("button", { name: "浏览器验收例会", exact: true })
    .click();
  await page.getByRole("button", { name: "＋ 新建会议", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "会议基本信息", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "参会人员", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", {
      name: "上期会议任务跟踪情况",
      exact: true,
    }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "本次会议议定事项", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "本次会议任务部署", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "编辑", exact: true }).click();
  await page
    .locator(".el-form-item")
    .filter({ hasText: "会议类型" })
    .locator(".el-select")
    .click();
  await page.getByRole("option", { name: "专项会议", exact: true }).click();
  await expect(
    page.getByRole("textbox", { name: "所属部门", exact: true }),
  ).toHaveValue("专项会议不需要填写部门");
  await expect(page.getByText("编制人（选填）", { exact: true })).toBeVisible();
  await page.screenshot({
    path: "test-results/theme-meeting-desktop.png",
    fullPage: true,
    animations: "disabled",
  });

  await page.setViewportSize({ width: 375, height: 812 });
  await expect(
    page.getByRole("heading", { name: "会议基本信息", exact: true }),
  ).toBeVisible();
  const documentWidth = await page.evaluate(
    () => document.documentElement.scrollWidth,
  );
  expect(documentWidth).toBeLessThanOrEqual(375);
  await page.screenshot({
    path: "test-results/theme-meeting-mobile.png",
    fullPage: true,
    animations: "disabled",
  });
});
