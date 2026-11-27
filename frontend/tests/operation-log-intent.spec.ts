import { expect, test, type Page } from "@playwright/test";

async function setup(page: Page) {
  const me = {
    id: "admin",
    name: "系统管理员",
    username: "admin",
    is_admin: true,
    active: true,
    department_id: "it",
  };
  const control = {
    requests: [] as { path: string; method: string; background: boolean }[],
    created: false,
    errors: [] as string[],
  };
  page.on("pageerror", (error) => control.errors.push(error.message));
  await page.addInitScript(() =>
    sessionStorage.setItem("token", "isolated-operation-intent"),
  );
  await page.route("**/api/**", async (route) => {
    const request = route.request(),
      url = new URL(request.url());
    control.requests.push({
      path: url.pathname,
      method: request.method(),
      background: request.headers()["x-operation-context"] === "background",
    });
    let body: any = {};
    if (url.pathname === "/api/me") body = me;
    else if (url.pathname === "/api/directory")
      body = {
        users: [me],
        departments: [{ id: "it", name: "信息部" }],
        series: [],
      };
    else if (url.pathname === "/api/auth/options") body = { wecom: false };
    else if (url.pathname === "/api/admin/config")
      body = { wecom_configured: false };
    else if (url.pathname === "/api/admin/native-todo")
      body = { allowed_users: [] };
    else if (url.pathname === "/api/admin/reminders")
      body =
        request.method() === "PUT"
          ? { ...request.postDataJSON(), cancelled_events: 0 }
          : { enabled: true, deadline_enabled: true, daily_enabled: false };
    else if (
      ["/api/admin/operations", "/api/admin/integration-checks"].includes(
        url.pathname,
      )
    )
      body = {};
    else if (
      [
        "/api/admin/notifications",
        "/api/admin/reassignment",
        "/api/admin/report-access",
        "/api/admin/employee-roles",
      ].includes(url.pathname)
    )
      body = [];
    else if (url.pathname === "/api/meeting-series")
      body = {
        items: control.created
          ? [
              {
                key: "series:new",
                name: "新例会",
                meeting_count: 0,
                historical_meeting_count: 0,
              },
            ]
          : [],
        total: control.created ? 1 : 0,
        meeting_total: 0,
      };
    else if (url.pathname === "/api/series" && request.method() === "POST") {
      control.created = true;
      body = { id: "new", name: "新例会", department_id: "it" };
    } else if (url.pathname === "/api/history-meeting-departments")
      body = { items: [], meeting_total: 0 };
    else if (url.pathname === "/api/meetings") body = { items: [], total: 0 };
    else
      throw new Error(
        `Unexpected request: ${request.method()} ${url.pathname}`,
      );
    await route.fulfill({ json: body });
  });
  return control;
}

test("请求标记按单次调用隔离，后台读取不影响同时发生的主动查看和写入", async ({
  page,
}) => {
  const control = await setup(page);
  await page.route("**/__operation-intent.html", (route) =>
    route.fulfill({
      contentType: "text/html",
      body: '<script type="module">import {api} from "/src/api.ts"; window.intentApi = api;</script>',
    }),
  );
  await page.goto("/__operation-intent.html");
  await page.waitForFunction(() => (window as any).intentApi);
  await page.evaluate(async () => {
    const api = (window as any).intentApi;
    await Promise.all([
      api("/meetings", "GET", undefined, { background: true }),
      api("/meetings"),
      api("/series", "POST", { name: "新例会" }, { background: true }),
    ]);
  });
  expect(
    control.requests
      .filter((r) => r.path === "/api/meetings")
      .map((r) => r.background)
      .sort(),
  ).toEqual([false, true]);
  expect(control.requests.find((r) => r.method === "POST")?.background).toBe(
    false,
  );
  expect(control.errors).toEqual([]);
});

test("系统管理保留主动查看和手动刷新，保存后的内部加载及辅助授权加载标为后台", async ({
  page,
}) => {
  const control = await setup(page);
  await page.goto("/settings");
  await expect(
    page.getByRole("button", { name: "保存提醒规则", exact: true }),
  ).toBeVisible();
  const reads = () =>
    control.requests.filter(
      (r) => r.method === "GET" && r.path.startsWith("/api/admin/"),
    );
  await expect.poll(() => reads().length).toBe(7);
  expect(
    reads()
      .filter((r) => !r.background)
      .map((r) => r.path),
  ).toEqual(["/api/admin/config"]);
  await page.getByRole("button", { name: "保存提醒规则", exact: true }).click();
  await expect
    .poll(() => control.requests.filter((r) => r.method === "PUT").length)
    .toBe(1);
  await expect.poll(() => reads().length).toBe(14);
  expect(control.requests.find((r) => r.method === "PUT")?.background).toBe(
    false,
  );
  expect(
    reads()
      .slice(7)
      .every((r) => r.background),
  ).toBe(true);
  await page.getByRole("button", { name: "刷新", exact: true }).click();
  await expect.poll(() => reads().length).toBe(21);
  expect(
    reads()
      .filter((r) => !r.background)
      .map((r) => r.path),
  ).toEqual(["/api/admin/config", "/api/admin/config"]);
  expect(control.errors).toEqual([]);
});

for (const route of ["/meetings", "/history-meetings"]) {
  test(`主动进入保留查看日志，定时与焦点刷新标为后台 ${route}`, async ({
    page,
  }) => {
    await page.clock.install();
    const control = await setup(page);
    await page.goto(route);
    const path =
      route === "/meetings" ? "/api/meeting-series" : "/api/meetings";
    await expect
      .poll(() => control.requests.filter((r) => r.path === path).length)
      .toBe(1);
    expect(control.requests.find((r) => r.path === path)?.background).toBe(
      false,
    );
    await page.clock.runFor(15001);
    await expect
      .poll(() => control.requests.filter((r) => r.path === path).length)
      .toBe(2);
    await page.evaluate(() => window.dispatchEvent(new Event("focus")));
    await page.clock.runFor(1);
    await expect
      .poll(() => control.requests.filter((r) => r.path === path).length)
      .toBe(3);
    expect(
      control.requests.filter((r) => r.path === path).map((r) => r.background),
    ).toEqual([false, true, true]);
    expect(control.errors).toEqual([]);
  });
}

test("新建系列后的重新加载都标为后台，手动查询仍记为主动操作", async ({
  page,
}) => {
  await page.clock.install();
  const control = await setup(page);
  await page.goto("/meetings");
  await expect
    .poll(
      () =>
        control.requests.filter((r) => r.path === "/api/meeting-series").length,
    )
    .toBe(1);
  await page
    .getByRole("button", { name: "＋ 新建会议系列", exact: true })
    .click();
  const prompt = page.getByRole("dialog", {
    name: "新建会议系列",
    exact: true,
  });
  await prompt.getByRole("textbox").fill("新例会");
  await prompt.getByRole("button", { name: "确定", exact: true }).click();
  await expect
    .poll(() => control.requests.filter((r) => r.method === "POST").length)
    .toBe(1);
  await expect
    .poll(
      () =>
        control.requests.filter((r) => r.path === "/api/meeting-series").length,
    )
    .toBe(2);
  await page.clock.runFor(200);
  await expect
    .poll(
      () =>
        control.requests.filter((r) => r.path === "/api/meeting-series").length,
    )
    .toBe(3);
  expect(
    control.requests
      .filter((r) => r.path === "/api/meeting-series")
      .map((r) => r.background),
  ).toEqual([false, true, true]);
  await page.getByPlaceholder("搜索会议系列名称").fill("新例会");
  await page.getByRole("button", { name: "查询", exact: true }).click();
  await expect
    .poll(
      () =>
        control.requests.filter((r) => r.path === "/api/meeting-series").length,
    )
    .toBe(4);
  expect(
    control.requests.filter((r) => r.path === "/api/meeting-series").at(-1)
      ?.background,
  ).toBe(false);
  expect(control.errors).toEqual([]);
});

for (const width of [1366, 390]) {
  test(`管理员可以按标签查看系统错误与排查信息 ${width}px`, async ({
    page,
  }, testInfo) => {
    await setup(page);
    await page.setViewportSize({ width, height: 900 });
    await page.route("**/api/me", (route) =>
      route.fulfill({
        json: {
          id: "admin",
          name: "系统管理员",
          username: "admin",
          is_admin: true,
          active: true,
          can_view_operation_logs: true,
        },
      }),
    );
    const entry = {
      id: "error-1",
      actor_name: "示例员工",
      actor_username: "employee1",
      operation: "保存会议草稿",
      target: "会议",
      summary: "请填写会议名称后再保存草稿",
      created_at: "2026-11-25T06:30:00Z",
      result: "failed",
      context: {
        error_source: "服务端",
        method: "POST",
        path: "/api/meetings",
        page: "/meetings/new",
        http_status: 422,
      },
    };
    await page.route("**/api/admin/operation-logs*", (route) => {
      const url = new URL(route.request().url());
      const items = url.searchParams.get("view") === "errors" ? [entry] : [];
      return route.fulfill({
        json: {
          items,
          total: items.length,
          categories: { meeting: "会议与任务单" },
        },
      });
    });
    await page.route("**/api/admin/operation-logs/error-1", (route) =>
      route.fulfill({ json: entry }),
    );
    await page.goto("/operation-logs");
    await page.getByText("系统错误日志", { exact: true }).click();
    await expect(
      page.getByText("请填写会议名称后再保存草稿", { exact: true }),
    ).toBeVisible();
    await page.getByRole("button", { name: "查看保存会议草稿详情" }).click();
    const detail = page.getByRole("dialog", { name: "错误详情" });
    await expect(
      detail.getByText("POST /api/meetings", { exact: true }),
    ).toBeVisible();
    await expect(detail.getByText("422", { exact: true })).toBeVisible();
    await page.screenshot({
      path: testInfo.outputPath(`error-log-${width}.png`),
      animations: "disabled",
    });
  });
}
