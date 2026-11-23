import { expect, test, type Page } from "@playwright/test";

async function fixture(page: Page, root = true, admin = true) {
  const me = {
    id: "me",
    name: root ? "系统管理员" : "其他管理员",
    username: root ? "admin" : "other",
    is_admin: admin,
    active: true,
    can_view_operation_logs: root && admin,
  };
  const requests: URL[] = [],
    errors: string[] = [];
  let fail = false;
  const log = {
    id: "publication",
    created_at: "2026-11-05T01:02:03Z",
    actor_name: "金丽君",
    actor_username: "jin",
    operation: "发布会议",
    target: "采购部周例会",
    summary: "已发布；通知 2 个渠道",
    sent_at: "2026-11-05T01:02:10Z",
    result: "success",
    action: "meeting.publish",
  };
  page.on("pageerror", (e) => errors.push(e.message));
  await page.addInitScript(() =>
    sessionStorage.setItem("token", "isolated-logs"),
  );
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    let body: any = {};
    if (url.pathname === "/api/me") body = me;
    else if (url.pathname === "/api/directory")
      body = { users: [me], departments: [], series: [] };
    else if (url.pathname === "/api/auth/options") body = { wecom: false };
    else if (url.pathname === "/api/admin/operation-logs") {
      requests.push(url);
      if (fail)
        return route.fulfill({
          status: 503,
          json: { detail: "日志服务暂时不可用，请重试" },
        });
      body = {
        items: url.searchParams.get("keyword") === "无匹配" ? [] : [log],
        total: 45,
        categories: { meeting: "会议与任务单", notification: "通知发送" },
      };
    } else if (url.pathname === "/api/admin/operation-logs/publication") {
      body = {
        ...log,
        context: { actor_name: "金丽君" },
        meeting_record: {
          created_at: "2026-11-04T07:32:10Z",
          created_by: "金丽君",
          meeting_date: "2026-11-05",
        },
        filters: [],
        changes: [{ field: "发送通知", before: "—", after: "是" }],
        deliveries: [
          {
            event_id: "mail",
            channel: "email",
            actual_recorded: true,
            state: "sent",
            sent_at: "2026-11-05T01:02:10Z",
            to: [{ name: "张三", address: "zhang@example.com" }],
            cc: [{ name: "主持人", address: "host@example.com" }],
            history: [
              {
                created_at: "2026-11-05T01:02:10Z",
                state: "sent",
                actual_to: ["zhang@example.com"],
                actual_cc: ["host@example.com"],
              },
            ],
          },
        ],
      };
    }
    await route.fulfill({ json: body });
  });
  await page.goto("/operation-logs");
  return {
    requests,
    errors,
    setFail(value: boolean) {
      fail = value;
    },
  };
}

for (const width of [1366, 390]) {
  test(`admin 查询、分页和打开通知详情 ${width}`, async ({
    page,
  }, testInfo) => {
    await page.setViewportSize({ width, height: 1000 });
    const control = await fixture(page);
    await expect(
      page.getByRole("heading", { name: "操作日志", exact: true }),
    ).toBeVisible();
    if (width < 768)
      await page
        .getByRole("button", { name: "展开导航栏", exact: true })
        .click();
    const nav = page.getByRole("navigation", { name: "主导航" });
    await expect(
      nav.getByRole("link", { name: "操作日志", exact: true }),
    ).toBeVisible();
    const names = await nav.getByRole("link").allTextContents();
    expect(names.indexOf("操作日志")).toBe(names.indexOf("系统管理") + 1);
    if (width < 768) await page.keyboard.press("Escape");
    await expect(
      page.getByRole("cell", { name: "采购部周例会", exact: true }),
    ).toBeVisible();
    await page.getByRole("textbox", { name: "日志关键词" }).fill("采购");
    await page.getByRole("button", { name: "查询", exact: true }).click();
    await expect
      .poll(() => control.requests.at(-1)?.searchParams.get("keyword"))
      .toBe("采购");
    await page
      .locator(".el-pager li.number")
      .filter({ hasText: /^2$/ })
      .click();
    await expect
      .poll(() => control.requests.at(-1)?.searchParams.get("page"))
      .toBe("2");
    await page.screenshot({
      path: testInfo.outputPath(`logs-${width}.png`),
      fullPage: true,
      animations: "disabled",
    });
    await page.getByRole("button", { name: "查看发布会议详情" }).click();
    const dialog = page.getByRole("dialog", { name: "操作详情" });
    await expect(dialog).toContainText("实际收件人（1）");
    await expect(dialog).toContainText("实际抄送人（1）");
    await expect(dialog).toContainText("会议创建信息");
    await expect(dialog).toContainText("创建人");
    await expect(dialog).toContainText("张三 <zhang@example.com>");
    await expect(dialog).toContainText("主持人 <host@example.com>");
    await expect(dialog).toContainText("实际收件邮箱：zhang@example.com");
    await expect(dialog).toContainText("不代表收件人已阅读");
    await dialog.screenshot({
      path: testInfo.outputPath(`detail-${width}.png`),
      animations: "disabled",
    });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBeTruthy();
    await dialog.getByRole("button", { name: "关闭", exact: true }).click();
    await page.getByRole("button", { name: "重置", exact: true }).click();
    await expect
      .poll(() => control.requests.at(-1)?.searchParams.get("page"))
      .toBe("1");
    expect(control.requests.at(-1)?.searchParams.has("keyword")).toBe(false);
    control.setFail(true);
    await page.getByRole("button", { name: "刷新", exact: true }).click();
    await expect(page.getByRole("alert")).toContainText("日志服务暂时不可用");
    control.setFail(false);
    await page.getByRole("button", { name: "刷新", exact: true }).click();
    await expect(page.getByRole("alert")).toHaveCount(0);
    await page.getByRole("textbox", { name: "日志关键词" }).fill("无匹配");
    await page.getByRole("textbox", { name: "日志关键词" }).press("Enter");
    await expect(
      page.getByText("暂无符合条件的操作记录", { exact: true }),
    ).toBeVisible();
    expect(control.errors).toEqual([]);
  });
}

for (const admin of [true, false]) {
  test(`${admin ? "其他系统管理员" : "员工"}不能看见入口或直接打开日志`, async ({
    page,
  }) => {
    const control = await fixture(page, false, admin);
    await expect(
      page.getByRole("heading", { name: "无权查看操作日志" }),
    ).toBeVisible();
    await expect(
      page.getByRole("link", { name: "操作日志", exact: true }),
    ).toHaveCount(0);
    expect(control.requests).toEqual([]);
    expect(control.errors).toEqual([]);
  });
}
