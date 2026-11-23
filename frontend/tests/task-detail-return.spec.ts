import { expect, test, type Page } from "@playwright/test";

function gate() {
  let release!: () => void;
  const promise = new Promise<void>((resolve) => (release = resolve));
  return { promise, release };
}

async function setup(page: Page, mode: "failed" | "pending" = "failed") {
  const pending = gate();
  let attempts = 0;
  const errors: string[] = [];
  const details: string[] = [];
  const me = { id: "owner", name: "袁可炜", active: true };
  const task = (id: string) => ({
    id,
    version: 1,
    content: id === "a" ? "核对会议任务归档" : "整理部门工作清单",
    source_meeting_id: null,
    issuer_name: "郑军鹏",
    issuer_id: "issuer",
    owner_ids: ["owner"],
    owner_names: [me.name],
    verifier_id: "issuer",
    verifier_name: "郑军鹏",
    due_date: "2026-11-20",
    status: "未到期",
    requests: [],
    closed_at: null,
    can_edit: true,
    allowed_owner_ids: ["owner", "issuer"],
    versions: Array.from({ length: 20 }, (_, i) => ({
      id: `v${i}`,
      number: i + 1,
      snapshot: { due_date: "2026-11-20" },
      reason: "历史任务变更记录",
    })),
  });
  page.on("pageerror", (e) => errors.push(e.message));
  await page.addInitScript(() =>
    sessionStorage.setItem("token", "task-return-test"),
  );
  await page.route("**/api/**", async (route) => {
    const request = route.request(),
      path = new URL(request.url()).pathname;
    let body: any;
    if (path === "/api/me") body = me;
    else if (path === "/api/auth/options") body = { wecom: false };
    else if (path === "/api/directory")
      body = {
        users: [me, { id: "issuer", name: "郑军鹏", active: true }],
        departments: [],
        series: [],
      };
    else if (path === "/api/tasks")
      body = { total: 2, items: [task("a"), task("b")] };
    else if (path.endsWith("/native-todo"))
      body = { enabled: false, link: null };
    else if (request.method() !== "GET") {
      attempts++;
      if (mode === "pending") {
        await pending.promise;
        body = {};
      } else
        return route.fulfill({
          status: 422,
          json: { detail: "首次提交失败，请核对任务版本" },
        });
    } else if (/^\/api\/tasks\/[ab]$/.test(path)) {
      const id = path.split("/").at(-1)!;
      details.push(id);
      body = task(id);
    } else throw new Error(`Unexpected request: ${request.method()} ${path}`);
    await route.fulfill({ json: body });
  });
  await page.goto("/tasks?id=a&native_confirm=0&reminder_day=2026-11-12");
  const drawer = page.getByRole("dialog", { name: "任务详情", exact: true });
  await expect(drawer).toBeVisible();
  return { drawer, pending, attempts: () => attempts, errors, details };
}

for (const width of [1366, 390]) {
  test(`failed first submission can return with a pinned button at ${width}px`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 900 });
    const controls = await setup(page);
    await controls.drawer
      .getByLabel("完成说明", { exact: true })
      .fill("任务已核对完成");
    await controls.drawer
      .getByRole("button", { name: "提交完成申请", exact: true })
      .click();
    await expect(
      page.getByText("首次提交失败，请核对任务版本", { exact: true }),
    ).toBeVisible();
    await expect(
      controls.drawer.getByLabel("完成说明", { exact: true }),
    ).toHaveValue("任务已核对完成");
    await controls.drawer.locator(".el-drawer__body").evaluate((body) => {
      body.scrollTop = body.scrollHeight;
    });
    const back = controls.drawer.getByRole("button", {
      name: "返回上一页",
      exact: true,
    });
    await expect(back).toBeInViewport();
    await page.screenshot({
      path: `../../.validation/task-detail-return-20261112/return-${width}.png`,
      animations: "disabled",
    });
    await back.click();
    await expect(controls.drawer).toBeHidden();
    await expect
      .poll(() => new URL(page.url()).searchParams.has("id"))
      .toBe(false);
    expect(new URL(page.url()).searchParams.has("native_confirm")).toBe(false);
    expect(new URL(page.url()).searchParams.get("reminder_day")).toBe(
      "2026-11-12",
    );
    await expect(
      page
        .getByRole("button", { name: "核对会议任务归档", exact: true })
        .filter({ visible: true }),
    ).toBeVisible();
    expect(controls.attempts()).toBe(1);
    expect(controls.errors).toEqual([]);
  });
}

test("returning during submission never reopens or replaces another task", async ({
  page,
}) => {
  const controls = await setup(page, "pending");
  await controls.drawer
    .getByLabel("完成说明", { exact: true })
    .fill("核对完成");
  await controls.drawer
    .getByRole("button", { name: "提交完成申请", exact: true })
    .click();
  await expect.poll(controls.attempts).toBe(1);
  await controls.drawer
    .getByRole("button", { name: "返回上一页", exact: true })
    .click();
  await expect(controls.drawer).toBeHidden();
  await page
    .getByRole("button", { name: "整理部门工作清单", exact: true })
    .filter({ visible: true })
    .click();
  await expect(
    controls.drawer.getByRole("heading", {
      name: "整理部门工作清单",
      exact: true,
    }),
  ).toBeVisible();
  await controls.drawer
    .getByLabel("完成说明", { exact: true })
    .fill("第二项任务的完成说明");
  controls.pending.release();
  await expect(
    page.getByText("完成申请已提交，等待验证", { exact: true }),
  ).toBeVisible();
  await expect(
    controls.drawer.getByRole("heading", {
      name: "整理部门工作清单",
      exact: true,
    }),
  ).toBeVisible();
  await expect(
    controls.drawer.getByRole("button", { name: "提交完成申请", exact: true }),
  ).toBeEnabled();
  await expect(
    controls.drawer.getByLabel("完成说明", { exact: true }),
  ).toHaveValue("第二项任务的完成说明");
  expect(controls.details).toEqual(["a", "b"]);
  expect(controls.errors).toEqual([]);
});

test("failed changes can return and Escape also clears the detail deep link", async ({
  page,
}) => {
  const controls = await setup(page);
  await controls.drawer
    .getByRole("button", { name: "调整期限 / 责任人", exact: true })
    .click();
  await controls.drawer
    .getByRole("button", { name: "保存变更", exact: true })
    .click();
  await expect(
    page.getByText("首次提交失败，请核对任务版本", { exact: true }),
  ).toBeVisible();
  await controls.drawer
    .getByRole("button", { name: "返回上一页", exact: true })
    .click();
  await expect(controls.drawer).toBeHidden();
  await page.reload();
  await expect(controls.drawer).toBeHidden();
  await page
    .getByRole("button", { name: "核对会议任务归档", exact: true })
    .filter({ visible: true })
    .click();
  await expect(controls.drawer).toBeVisible();
  await controls.drawer.press("Escape");
  await expect(controls.drawer).toBeHidden();
  expect(controls.errors).toEqual([]);
});
