import { test, expect } from "@playwright/test";
test("真实接口：新建会议、下发、申请、验证、导出和移动页面", async ({
  page,
}) => {
  await page.addInitScript(() => {
    Object.defineProperty(Crypto.prototype, "randomUUID", {
      configurable: true,
      value: undefined,
    });
  });
  const errors: string[] = [];
  page.on("pageerror", (e) => {
    if (!e.stack?.includes("chrome-extension://")) errors.push(e.message);
  });
  await page.goto("/");
  await page.getByLabel("账号", { exact: true }).fill("browser-test");
  await page.getByLabel("密码", { exact: true }).fill("local-test-only-123");
  await page.getByRole("button", { name: "进入工作台" }).click();
  await expect(
    page.getByRole("heading", { name: "会议纪要与任务单" }),
  ).toBeVisible();
  await expect(
    page.getByRole("combobox", { name: "发布状态", exact: true }),
  ).toBeVisible();
  await expect(page.getByRole("heading", { name: /会议归档/ })).toBeVisible();
  await expect(page.getByText("待校验历史资料", { exact: true })).toHaveCount(
    0,
  );
  await page.getByRole("button", { name: "＋ 新建会议", exact: true }).click();
  await page.getByRole("button", { name: "编辑", exact: true }).click();
  await page.getByLabel("会议名称", { exact: true }).fill("浏览器闭环验收会议");
  await page.getByRole("button", { name: "＋ 新增工作" }).click();
  await expect(page.locator(".meeting-plan-table tbody tr")).toHaveCount(1);
  await expect(page.getByText("审核人", { exact: true })).toHaveCount(0);
  await expect(
    page.getByRole("heading", { name: "会议附件", exact: true }),
  ).toBeVisible();
  const row = page.locator(".meeting-plan-table tbody tr").first();
  await row.locator("textarea").first().fill("完成浏览器端到端验收");
  await row
    .getByRole("textbox", { name: "输出物", exact: true })
    .fill("验收记录表");
  await expect(page.getByText("验收要求", { exact: true })).toHaveCount(0);
  await row.locator(".el-select").first().click();
  await page.getByRole("option", { name: "验收管理员", exact: true }).click();
  await page.keyboard.press("Escape");
  await row.locator(".el-date-editor input").fill("2026-12-31");
  await page.keyboard.press("Enter");
  await page.locator(".meeting-plan-table").screenshot({
    path: "test-results/meeting-plan-table.png",
    animations: "disabled",
  });
  await page.getByRole("heading", { name: "在线会议编辑" }).click();
  await page.getByRole("button", { name: "保存草稿", exact: true }).click();
  await expect(page).toHaveURL(/\/meetings\/(?!new)[\w-]+$/);
  await page.reload();
  await page.getByRole("button", { name: "编辑", exact: true }).click();
  await expect(page.getByLabel("会议名称", { exact: true })).toHaveValue(
    "浏览器闭环验收会议",
  );
  await page.getByRole("button", { name: "预览并发布" }).click();
  await expect(
    page.locator('iframe[title="发布前 PDF 纪要预览"]'),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "确认发布", exact: true }),
  ).toBeEnabled();
  const previewDownload = page.waitForEvent("download");
  await page.getByRole("button", { name: "下载草稿任务单（Excel）" }).click();
  expect((await previewDownload).suggestedFilename()).toBe(
    "草稿预览-任务单.xlsx",
  );
  await page.screenshot({
    path: "test-results/publication-preview.png",
    fullPage: true,
    animations: "disabled",
  });
  await page.getByRole("button", { name: "确认发布", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "会议纪要", exact: true }),
  ).toBeVisible();
  await expect(page.getByText("编辑申请待主持人审批")).toHaveCount(0);
  await page
    .getByRole("button", { name: "本会议考核明细", exact: true })
    .click();
  await expect(page.getByText("本版本没有上期回顾考核")).toBeVisible();
  await expect(
    page.getByRole("button", { name: "会议条目与当前任务关联" }),
  ).toHaveCount(0);
  await expect(page.locator(".meeting-plan-table")).toContainText(
    "完成浏览器端到端验收",
  );
  await page
    .getByRole("button", { name: "相关任务通知（最近 200 条）", exact: true })
    .click();
  await expect(
    page.getByRole("cell", { name: "新的会议任务", exact: true }),
  ).toBeVisible();
  await page.screenshot({
    path: "test-results/meeting-traceability.png",
    fullPage: true,
    animations: "disabled",
  });
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "PDF 纪要", exact: true }).click();
  expect((await download).suggestedFilename()).toContain(".pdf");
  await page.getByRole("link", { name: "我的待办", exact: true }).click();
  await page
    .getByRole("button")
    .filter({ hasText: "完成浏览器端到端验收" })
    .click();
  await page.getByRole("button", { name: "设置周期任务", exact: true }).click();
  await page
    .getByRole("textbox", { name: "周期设置依据", exact: true })
    .fill("每周按会议要求独立验收");
  await page.getByRole("button", { name: "保存周期规则", exact: true }).click();
  await expect(page.getByText("每周生成独立任务 · 已启用")).toBeVisible();
  await page.getByRole("button", { name: "维护周期规则", exact: true }).click();
  await page
    .locator(".el-form-item")
    .filter({ hasText: "继续生成" })
    .locator(".el-switch")
    .click();
  await page
    .getByRole("textbox", { name: "周期设置依据", exact: true })
    .fill("验收后暂停后续自动生成");
  await page.getByRole("button", { name: "保存周期规则", exact: true }).click();
  await expect(page.getByText("每周生成独立任务 · 已暂停或结束")).toBeVisible();
  const recurrencePanel = page.locator("section").filter({
    has: page.getByRole("heading", { name: "周期工作", exact: true }),
  });
  await recurrencePanel.scrollIntoViewIfNeeded();
  await expect(page.locator(".el-message")).toHaveCount(0);
  await recurrencePanel.screenshot({
    path: "test-results/recurring-task.png",
    animations: "disabled",
  });
  await page
    .getByLabel("完成说明", { exact: true })
    .fill("浏览器操作验证完成，证据齐全。");
  await page.getByRole("button", { name: "提交完成申请" }).click();
  await expect(
    page
      .locator(".task-card-bottom")
      .filter({ hasText: "已提交申请 · 待验证" }),
  ).toBeVisible();
  await page.keyboard.press("Escape");
  await page.getByRole("link", { name: "待我验证", exact: true }).click();
  await expect(page.getByText("验收要求", { exact: true })).toHaveCount(0);
  await expect(page.getByText("验收记录表", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "验证通过并关闭" }).click();
  await page.locator(".el-message-box textarea").fill("核对通过");
  await page
    .locator(".el-message-box")
    .getByRole("button", { name: "确定", exact: true })
    .click();
  await expect(page.getByText("没有待处理的验证申请")).toBeVisible();
  await page.getByRole("link", { name: "我的待办", exact: true }).click();
  await expect(page.locator(".task-card .status-tag")).toHaveText("关闭");
  await page.getByRole("link", { name: "导入历史会议", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: /从文件提取的会议/ }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "查看全部上传文件", exact: true })
    .click();
  await page.getByLabel("搜索导入文件", { exact: true }).fill("fixture.xlsx");
  const duplicateRows = page.locator(".source-row").filter({
    has: page.getByText("重复上传", { exact: true }),
  });
  const initialDuplicates = await duplicateRows.count();
  expect(initialDuplicates).toBeGreaterThan(0);
  await expect(
    page.getByText(/若已经解密，请确认保存后使用“另存为”/).first(),
  ).toBeVisible();
  const duplicateSource = duplicateRows.first();
  await duplicateSource
    .getByRole("button", { name: "删除导入记录", exact: true })
    .click();
  await page
    .locator(".el-message-box input")
    .fill("浏览器测试删除重复上传记录");
  await page
    .locator(".el-message-box")
    .getByRole("button", { name: "确认删除", exact: true })
    .click();
  await expect(duplicateRows).toHaveCount(initialDuplicates - 1);
  await expect(
    page
      .locator(".source-row")
      .getByText("fixture.xlsx", { exact: true })
      .first(),
  ).toBeVisible();
  await page.locator(".el-dialog").screenshot({
    path: "test-results/import-source-management.png",
    animations: "disabled",
  });
  await page.keyboard.press("Escape");
  await page.locator(".el-table__body .el-button").first().click();
  await expect(
    page.getByRole("heading", { name: "历史会议确认" }),
  ).toBeVisible();
  await page
    .getByRole("heading", { name: "会议基本信息", exact: true })
    .scrollIntoViewIfNeeded();
  await expect(page.locator(".historical-review-row")).toHaveCount(63);
  await expect(page.getByText("责任人唯一匹配")).toHaveCount(0);
  await expect(page.getByLabel("项目 / 类别")).toHaveCount(0);
  const historicalPlan = page.locator("section").filter({
    has: page.getByRole("heading", {
      name: "本次会议任务部署",
      exact: true,
    }),
  });
  await expect(historicalPlan.locator(".meeting-plan-table")).toBeVisible();
  await expect(
    historicalPlan.getByText("验收要求", { exact: true }),
  ).toHaveCount(0);
  await historicalPlan.scrollIntoViewIfNeeded();
  await historicalPlan.screenshot({
    path: "test-results/historical-plan-table.png",
    animations: "disabled",
  });
  await expect(page.locator(".el-message")).toHaveCount(0);
  await page.screenshot({
    path: "test-results/historical-review-desktop.png",
    animations: "disabled",
  });
  await page.setViewportSize({ width: 375, height: 812 });
  await expect
    .poll(() => page.evaluate(() => document.documentElement.scrollWidth))
    .toBeLessThanOrEqual(375);
  await page.screenshot({
    path: "test-results/historical-review-mobile.png",
    animations: "disabled",
  });
  await page.setViewportSize({ width: 1280, height: 720 });
  await page
    .getByRole("button", { name: "关联同一工作", exact: true })
    .first()
    .click();
  await expect(
    page.getByRole("dialog", { name: "人工确认历史任务关联" }),
  ).toBeVisible();
  await expect(
    page.getByRole("cell", { name: /历史关联基准会议/ }),
  ).toBeVisible();
  await page
    .getByRole("textbox", { name: "历史关联依据", exact: true })
    .fill("工作内容和负责人一致");
  await page
    .getByRole("button", { name: "确认关联", exact: true })
    .first()
    .click();
  await expect(page.getByText(/已关联：/).first()).toBeVisible();
  await page.getByRole("button", { name: "暂存修改" }).click();
  await expect(page.getByRole("button", { name: "暂存修改" })).not.toHaveClass(
    /is-loading/,
  );
  await page.getByRole("button", { name: "← 返回导入列表" }).click();
  await expect(
    page.getByRole("heading", { name: /从文件提取的会议/ }),
  ).toBeVisible();
  await page.locator(".el-table__body .el-button").first().click();
  await expect(page.getByText(/已关联：/).first()).toBeVisible();
  await page.locator(".historical-review-row").first().scrollIntoViewIfNeeded();
  await expect(page.locator(".el-message")).toHaveCount(0);
  await page.locator(".historical-review-row").first().screenshot({
    path: "test-results/historical-link.png",
    animations: "disabled",
  });
  await page.getByRole("button", { name: "← 返回导入列表" }).click();
  await page.screenshot({
    path: "test-results/desktop-validation.png",
    fullPage: true,
    animations: "disabled",
  });
  await page.getByRole("link", { name: "系统管理", exact: true }).click();
  await page
    .getByRole("button", { name: "检查 SMTP 连接", exact: true })
    .click();
  await expect(
    page.getByText("必要参数：SMTP 主机或发件地址尚未配置", { exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "检查企业微信连接", exact: true })
    .click();
  await expect(
    page.getByText("必要参数：企业微信 CorpID、AgentID、Secret 尚未配置完整", {
      exact: true,
    }),
  ).toBeVisible();
  await page.getByRole("tab", { name: "运行监控与告警" }).click();
  await expect(
    page.getByText("监控心跳缺失或已过期，请检查 monitor 服务"),
  ).toBeVisible();
  await expect(
    page.getByText(
      "外部告警尚未配置。当前仅记录本机检查结果，上线前需配置企业告警接收端并验证送达。",
    ),
  ).toBeVisible();
  await page.screenshot({
    path: "test-results/operations-unconfigured.png",
    fullPage: true,
    animations: "disabled",
  });
  await page.getByRole("tab", { name: "通讯录与别名" }).click();
  await expect(
    page.getByRole("heading", { name: "通讯录同步记录" }),
  ).toBeVisible();
  await expect(
    page.getByRole("columnheader", { name: "系统角色" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "设为普通成员" }),
  ).toBeVisible();
  await page
    .getByRole("textbox", { name: "人员别名", exact: true })
    .fill("验收别名");
  await page
    .getByRole("tabpanel", { name: "通讯录与别名" })
    .locator(".filter-row .el-select")
    .first()
    .click();
  await page.getByRole("option", { name: "验收管理员", exact: true }).click();
  await page.getByRole("button", { name: "保存别名", exact: true }).click();
  await expect(
    page.getByRole("cell", { name: "验收别名", exact: true }),
  ).toBeVisible();
  await page
    .getByRole("textbox", { name: "搜索人员别名", exact: true })
    .fill("验收别名");
  await page.getByRole("button", { name: "删除", exact: true }).click();
  await page.locator(".el-message-box input").fill("浏览器验收完毕删除");
  await page
    .locator(".el-message-box")
    .getByRole("button", { name: "确定", exact: true })
    .click();
  await expect(
    page.getByRole("cell", { name: "验收别名", exact: true }),
  ).toHaveCount(0);
  await page.screenshot({
    path: "test-results/directory-roles.png",
    fullPage: true,
    animations: "disabled",
  });
  await page.getByRole("link", { name: "统计报表", exact: true }).click();
  await expect(
    page.getByRole("combobox", { name: "筛选人员", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("combobox", { name: "筛选会议类型", exact: true }),
  ).toBeVisible();
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto("/tasks");
  await expect(
    page.getByRole("heading", { name: "我的待办", exact: true }),
  ).toBeVisible();
  await page.screenshot({
    path: "test-results/mobile-tasks.png",
    fullPage: true,
    animations: "disabled",
  });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBeTruthy();
  expect(errors).toEqual([]);
});
