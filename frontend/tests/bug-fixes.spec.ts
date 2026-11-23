import { expect, test } from "@playwright/test";

function gate() {
  let release!: () => void;
  const promise = new Promise<void>((resolve) => (release = resolve));
  return { promise, release };
}

test("首次保存期间的新输入保留并自动继续保存，失败时仍保留未保存内容", async ({
  page,
}) => {
  const started = gate(),
    firstResponse = gate();
  const user = {
    id: "isolated-user",
    name: "隔离管理员",
    is_admin: true,
    department_id: "isolated-department",
    active: true,
  };
  let stored: any,
    getCount = 0,
    postCount = 0;
  const updates: any[] = [];
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.addInitScript(() =>
    sessionStorage.setItem("token", "isolated-token"),
  );
  await page.route("**/api/**", async (route) => {
    const request = route.request(),
      path = new URL(request.url()).pathname;
    let body: any;
    if (path === "/api/me") body = user;
    else if (path === "/api/directory")
      body = {
        users: [user],
        departments: [{ id: user.department_id, name: "隔离部门" }],
        series: [],
      };
    else if (path === "/api/meeting-people")
      body = {
        users: [user],
        departments: [{ id: user.department_id, name: "隔离部门" }],
      };
    else if (path === "/api/auth/options") body = { wecom: false };
    else if (path === "/api/meetings" && request.method() === "POST") {
      postCount += 1;
      stored = request.postDataJSON();
      started.release();
      await firstResponse.promise;
      body = {
        id: "saved-meeting",
        draft: stored,
        draft_version: 1,
        published_version: 0,
        has_draft: true,
      };
    } else if (
      path === "/api/meetings/saved-meeting" &&
      request.method() === "PUT"
    ) {
      const value = request.postDataJSON();
      updates.push(value);
      if (updates.length === 1) {
        await route.fulfill({
          status: 503,
          contentType: "application/json",
          body: JSON.stringify({ detail: "隔离模拟保存失败" }),
        });
        return;
      }
      stored = value.draft;
      body = {
        id: "saved-meeting",
        draft: stored,
        draft_version: 2,
        published_version: 0,
        has_draft: true,
      };
    } else if (path === "/api/meetings/saved-meeting") {
      getCount += 1;
      body = {
        id: "saved-meeting",
        draft: stored,
        draft_version: 1,
        published_version: 0,
        has_draft: true,
        can_edit: true,
        versions: [],
        attachments: [],
      };
    } else
      throw new Error(`Unexpected mock request: ${request.method()} ${path}`);
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(body),
    });
  });
  await page.goto("/meetings/new?series=series:isolated-series");
  await page
    .locator(".meeting-info-card")
    .getByRole("button", { name: "编辑", exact: true })
    .click();
  const title = page.getByRole("textbox", { name: "会议名称", exact: true });
  await title.fill("首次保存标题");
  await page.getByRole("button", { name: "保存草稿", exact: true }).click();
  await started.promise;
  await title.fill("保存期间的新输入");
  firstResponse.release();
  await expect(page).toHaveURL(/meetings\/saved-meeting\?series=/);
  await expect(title).toHaveValue("保存期间的新输入");
  await expect.poll(() => updates.length).toBe(1);
  await expect(
    page.getByRole("alert").filter({ hasText: "草稿未保存：隔离模拟保存失败" }),
  ).toBeVisible();
  await expect(title).toHaveValue("保存期间的新输入");
  await expect(page.getByText("有未保存内容", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "保存草稿", exact: true }).click();
  await expect.poll(() => updates.length).toBe(2);
  expect(updates[1].draft.title).toBe("保存期间的新输入");
  expect(updates[1].expected_version).toBe(1);
  expect(postCount).toBe(1);
  expect(getCount).toBe(0);
  await expect(
    page.getByRole("button", { name: "删除会议", exact: true }),
  ).toBeVisible();
  expect(errors).toEqual([]);
});

test("任务详情只接受最后点击的响应，关闭抽屉后旧响应不能重开", async ({
  page,
}) => {
  const started = gate(),
    firstResponse = gate(),
    startedAgain = gate(),
    anotherResponse = gate();
  const user = {
    id: "isolated-user",
    name: "隔离成员",
    active: true,
    is_admin: false,
  };
  const task = (id: string) => ({
    id,
    code: id,
    content: id === "A" ? "隔离任务甲" : "隔离任务乙",
    owner_ids: [user.id],
    owner_names: [user.name],
    verifier_id: user.id,
    verifier_name: user.name,
    due_date: null,
    status: "未到期",
    version: 1,
    requests: [],
    versions: [],
    closed_at: null,
    can_edit: false,
  });
  let aRequests = 0;
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.addInitScript(() =>
    sessionStorage.setItem("token", "isolated-token"),
  );
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    let body: any;
    if (path === "/api/me") body = user;
    else if (path === "/api/directory")
      body = { users: [user], departments: [], series: [] };
    else if (path === "/api/auth/options") body = { wecom: false };
    else if (path === "/api/tasks") body = [task("A"), task("B")];
    else if (path.endsWith("/native-todo")) body = { enabled: false };
    else if (path === "/api/tasks/A") {
      aRequests += 1;
      (aRequests === 1 ? started : startedAgain).release();
      await (aRequests === 1 ? firstResponse : anotherResponse).promise;
      body = task("A");
    } else if (path === "/api/tasks/B") body = task("B");
    else throw new Error(`Unexpected mock request: ${path}`);
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(body),
    });
  });
  await page.goto("/tasks");
  const drawer = page.locator(".el-drawer:visible");
  const oldResponse = page.waitForResponse(
    (response) => new URL(response.url()).pathname === "/api/tasks/A",
  );
  await page
    .locator(".task-table .task-title-button")
    .filter({ hasText: "隔离任务甲" })
    .click();
  await started.promise;
  await page
    .locator(".task-table .task-title-button")
    .filter({ hasText: "隔离任务乙" })
    .click();
  await expect(
    drawer.getByRole("heading", { name: "隔离任务乙", exact: true }),
  ).toBeVisible();
  await expect(drawer.getByText("设置周期任务", { exact: true })).toHaveCount(
    0,
  );
  await expect(drawer.locator(".recurrence-panel")).toHaveCount(0);
  firstResponse.release();
  await (await oldResponse).finished();
  await page.evaluate(async () => {
    await new Promise(requestAnimationFrame);
    await new Promise(requestAnimationFrame);
  });
  await expect(
    drawer.getByRole("heading", { name: "隔离任务乙", exact: true }),
  ).toBeVisible();
  const pending = page.waitForResponse(
    (response) => new URL(response.url()).pathname === "/api/tasks/A",
  );
  // Change the query on the same route while its drawer is open, as a detail link can do.
  await page.evaluate(() => {
    history.pushState({}, "", "/tasks?id=A");
    window.dispatchEvent(new PopStateEvent("popstate"));
  });
  await startedAgain.promise;
  await drawer.locator(".el-drawer__close-btn").click();
  anotherResponse.release();
  await (await pending).finished();
  await page.evaluate(async () => {
    await new Promise(requestAnimationFrame);
    await new Promise(requestAnimationFrame);
  });
  await expect(drawer).not.toBeVisible();
  expect(errors).toEqual([]);
});
