import { test, expect } from "@playwright/test";

test("真实接口：系列内新建、手机编辑、预览发布、待办申请和验证闭环", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/");
  await page.getByLabel("账号", { exact: true }).fill("browser-test");
  await page.getByLabel("密码", { exact: true }).fill("local-test-only-123");
  await page.getByRole("button", { name: "进入工作台" }).click();
  await page
    .getByRole("button", { name: "浏览器验收例会", exact: true })
    .click();
  await page.getByRole("button", { name: "＋ 新建会议", exact: true }).click();
  await page
    .getByRole("dialog", { name: "新建会议", exact: true })
    .getByRole("textbox", { name: "会议名称", exact: true })
    .fill("真实闭环验收会议");
  await page
    .getByRole("button", { name: "确认并填写任务单", exact: true })
    .click();
  await page.getByRole("button", { name: "＋ 新增工作", exact: true }).click();
  const row = page.locator(".meeting-plan-table tbody tr").first();
  await row
    .getByRole("textbox", { name: "工作内容及要求", exact: true })
    .fill("完整核对供应商资料并形成验收记录");
  const owner = row.getByRole("combobox", { name: "责任人", exact: true });
  await owner.fill("验收管理员");
  await page.getByRole("option", { name: "验收管理员", exact: true }).click();
  await page.keyboard.press("Escape");
  await row
    .getByRole("combobox", { name: "计划完成时间", exact: true })
    .fill("2026-12-31");
  await page.keyboard.press("Enter");
  await row
    .getByRole("textbox", { name: "输出物", exact: true })
    .fill("验收记录表");
  await page.setViewportSize({ width: 390, height: 844 });
  if (
    await row
      .getByRole("button", { name: "展开第1项任务", exact: true })
      .count()
  )
    await row
      .getByRole("button", { name: "展开第1项任务", exact: true })
      .click();
  expect(
    await row.evaluate(
      (element) => element.scrollWidth <= element.clientWidth + 1,
    ),
  ).toBeTruthy();
  for (const label of ["责任人", "计划完成时间", "验证人", "输出物", "备注"]) {
    await expect(row.locator(`[data-label="${label}"]`)).toBeVisible();
  }
  await row.screenshot({
    path: "../../.validation/system-fixes-20261105/mobile-editor-real.png",
  });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.getByRole("button", { name: "保存草稿", exact: true }).click();
  await expect(page).toHaveURL(/\/meetings\/(?!new)[\w-]+/);
  await page.getByRole("button", { name: "预览并发布", exact: true }).click();
  await expect(
    page.getByRole("region", { name: "发布前 PDF 纪要预览" }),
  ).toBeVisible();
  const dialog = page.getByRole("dialog", {
    name: "确认发布会议纪要",
    exact: true,
  });
  const notifications = dialog.getByRole("checkbox", {
    name: "发送企业微信通知及纪要邮件",
    exact: true,
  });
  if (await notifications.isChecked())
    await dialog
      .getByText("发送企业微信通知及纪要邮件", { exact: true })
      .click();
  await expect(dialog.locator("canvas").first()).toBeVisible();
  await expect(page.locator(".el-message")).toHaveCount(0);
  await page.screenshot({
    path: "../../.validation/system-fixes-20261105/desktop-preview-real.png",
  });
  const download = page.waitForEvent("download");
  await dialog
    .getByRole("button", { name: "下载草稿任务单（Excel）", exact: true })
    .click();
  expect((await download).suggestedFilename()).toContain(".xlsx");
  await dialog.getByRole("button", { name: "确认发布", exact: true }).click();
  const publishOnly = page.getByRole("button", {
    name: "仅发布，不发送通知",
    exact: true,
  });
  const publishedHeading = page.getByRole("heading", {
    name: "会议纪要",
    exact: true,
  });
  await expect(publishOnly.or(publishedHeading)).toBeVisible();
  if (await publishOnly.isVisible()) await publishOnly.click();
  await expect(
    page.getByRole("heading", { name: "会议纪要", exact: true }),
  ).toBeVisible();
  await page.getByRole("link", { name: "我的待办", exact: true }).click();
  await page
    .locator(".task-table .task-title-button")
    .filter({ hasText: "完整核对供应商资料" })
    .click();
  await page.getByLabel("完成说明", { exact: true }).fill("隔离验收通过");
  await page.getByRole("button", { name: "提交完成申请", exact: true }).click();
  await expect(page.locator(".task-table .task-pending")).toContainText(
    "待验证",
  );
  await page.keyboard.press("Escape");
  await page.getByRole("link", { name: "待我验证", exact: true }).click();
  await page
    .getByRole("button", { name: "验证通过并关闭", exact: true })
    .click();
  await page.locator(".el-message-box textarea").fill("核对完成");
  await page
    .locator(".el-message-box")
    .getByRole("button", { name: "确定", exact: true })
    .click();
  await expect(page.getByText("没有待处理的验证申请")).toBeVisible();
  await page.getByRole("link", { name: "我的待办", exact: true }).click();
  await expect(page.locator(".task-table .task-row")).toHaveCount(0);
  await expect(page.getByText("当前筛选下没有待办")).toBeVisible();
  expect(errors).toEqual([]);
});
