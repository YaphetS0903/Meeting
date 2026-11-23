import { expect, test, type Page } from "@playwright/test";
import { fileURLToPath } from "node:url";

async function fixture(page: Page, mode: "edit" | "read" | "single" = "edit") {
  const me = {
    id: "host",
    name: "主持人",
    active: true,
    is_admin: false,
    department_id: "dept",
    email: "host@example.test",
  };
  const people = ["王胜", "杨洪奇", "赵永谦", "马军平", "王三虎"].map(
    (name, index) => ({
      id: `p${index}`,
      name,
      active: true,
      department_id: "dept",
      email: `p${index}@example.test`,
    }),
  );
  let draft: any = {
    title: "管理层周例会",
    kind: "department",
    department_id: "dept",
    series_id: "series",
    host_id: "host",
    recorder_id: "host",
    meeting_date: "2026-11-13",
    attendee_ids: [],
    online_attendee_ids: [],
    attachment_ids: [],
    recipient_ids: [],
    cc_ids: [],
    cc_emails: [],
    items: [
      {
        id: "mixed",
        section: "review",
        content: "直接人工效率27年预算目标",
        owner_ids:
          mode === "single" ? ["p0"] : people.map((person) => person.id),
        due_date: "2026-11-11",
        status: mode === "read" ? "部分完成" : "未完成",
        owner_results:
          mode === "read"
            ? Object.fromEntries(
                people.map((person, index) => [
                  person.id,
                  index < 2 ? "已完成" : "未完成",
                ]),
              )
            : {},
        task_id: "task",
        task_version: 1,
        category: "",
        requirements: "",
        notes: "",
        deliverable: "",
      },
    ],
  };
  let version = 1;
  const writes: any[] = [],
    previews: any[] = [],
    errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.addInitScript(() =>
    sessionStorage.setItem("token", "isolated-partial-results"),
  );
  await page.route("**/api/**", async (route) => {
    const request = route.request(),
      path = new URL(request.url()).pathname;
    let body: any = [];
    if (path === "/api/me") body = me;
    else if (path === "/api/auth/options") body = { wecom: false };
    else if (path === "/api/directory")
      body = {
        users: [me, ...people],
        departments: [{ id: "dept", name: "技术部" }],
        series: [{ id: "series", name: "管理层周例会", department_id: "dept" }],
      };
    else if (path === "/api/meeting-people") body = { users: [me, ...people] };
    else if (path.startsWith("/api/native-todo/")) body = { enabled: false };
    else if (path === "/api/meetings/partial/preview") {
      previews.push(structuredClone(draft));
      return route.fulfill({
        path: fileURLToPath(
          new URL("./fixtures/preview-minutes.pdf", import.meta.url),
        ),
        contentType: "application/pdf",
      });
    } else if (path === "/api/meetings/partial") {
      if (request.method() === "PUT") {
        expect(mode).not.toBe("read");
        expect(request.postDataJSON().expected_version).toBe(version);
        draft = request.postDataJSON().draft;
        version++;
        writes.push(structuredClone(draft));
      }
      body = {
        id: "partial",
        ...draft,
        draft,
        draft_version: version,
        published_version: mode === "read" ? 1 : 0,
        has_draft: mode !== "read",
        can_edit: mode !== "read",
        attachments: [],
        assessments: {},
        versions: mode === "read" ? [{ number: 1, snapshot: draft }] : [],
      };
    } else throw new Error(`Unexpected request ${request.method()} ${path}`);
    return route.fulfill({ json: body });
  });
  await page.goto("/meetings/partial");
  const row = page
    .locator("tbody tr")
    .filter({ hasText: "直接人工效率27年预算目标" })
    .first();
  await expect(row).toBeVisible();
  return { row, people, writes, previews, errors, draft: () => draft };
}

async function openPartial(page: Page, row: any) {
  await row.locator(".review-result .el-select").click();
  await page.getByRole("option", { name: "部分完成", exact: true }).click();
  return page.getByRole("dialog", { name: "按责任人评定", exact: true });
}

for (const width of [1366, 390]) {
  test(`部分完成逐人评定、保存重开和未完成人员延续 ${width}`, async ({
    page,
  }, testInfo) => {
    await page.setViewportSize({ width, height: 900 });
    const control = await fixture(page),
      dialog = await openPartial(page, control.row);
    await dialog.getByRole("button", { name: "确认结果", exact: true }).click();
    await expect(dialog.getByRole("alert")).toHaveText(
      "请为每位责任人选择已完成或未完成",
    );
    for (let i = 0; i < 5; i++)
      await dialog
        .getByRole("radiogroup", {
          name: `${control.people[i].name}的完成结果`,
          exact: true,
        })
        .getByRole("radio", { name: i < 2 ? "已完成" : "未完成", exact: true })
        .press("Space");
    await expect(dialog.getByRole("status")).toHaveText("已完成 2 人／共 5 人");
    await dialog.screenshot({
      path: testInfo.outputPath(`partial-dialog-${width}.png`),
      animations: "disabled",
    });
    await dialog.getByRole("button", { name: "确认结果", exact: true }).click();
    await expect(dialog).not.toBeVisible();
    await expect(control.row).toContainText("部分完成（2/5）");
    await page.getByRole("button", { name: "保存草稿", exact: true }).click();
    await expect.poll(() => control.writes.length).toBeGreaterThan(0);
    await page.reload();
    await expect(control.row).toContainText("部分完成（2/5）");
    await control.row
      .getByRole("button", { name: "查看／修改", exact: true })
      .click();
    await expect(
      dialog
        .getByRole("radiogroup", { name: "王胜的完成结果", exact: true })
        .getByRole("radio", { name: "已完成", exact: true }),
    ).toBeChecked();
    await dialog.getByRole("button", { name: "取消", exact: true }).click();
    await control.row
      .getByRole("button", { name: "延续安排", exact: true })
      .click();
    const extension = page.getByRole("dialog", {
      name: "延续安排",
      exact: true,
    });
    await extension
      .getByRole("combobox", { name: "延续日期", exact: true })
      .fill("2026-12-20");
    await extension
      .getByRole("combobox", { name: "延续日期", exact: true })
      .press("Enter");
    await extension
      .getByRole("button", { name: "确认延续", exact: true })
      .click();
    await page.getByRole("button", { name: "保存草稿", exact: true }).click();
    await expect.poll(() => control.draft().items.length).toBe(2);
    expect(control.draft().items[1].owner_ids).toEqual(["p2", "p3", "p4"]);
    expect(control.draft().items[1].owner_results).toEqual({});
    expect(control.draft().items[0].status).toBe("部分完成");
    await page.getByRole("button", { name: "预览并发布", exact: true }).click();
    await expect.poll(() => control.previews.length).toBe(1);
    expect(control.previews[0].items[0].owner_results.p0).toBe("已完成");
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    expect(control.errors).toEqual([]);
  });
}

test("取消评定不改原结果；统一改为未到期时清除逐人结果", async ({ page }) => {
  const control = await fixture(page),
    dialog = await openPartial(page, control.row);
  await dialog
    .getByRole("radiogroup", { name: "王胜的完成结果", exact: true })
    .getByRole("radio", { name: "已完成", exact: true })
    .press("Space");
  await dialog.getByRole("button", { name: "取消", exact: true }).click();
  await page.getByRole("button", { name: "保存草稿", exact: true }).click();
  expect(control.draft().items[0].status).toBe("未完成");
  await control.row.locator(".review-result .el-select").click();
  await page.getByRole("option", { name: "未到期", exact: true }).click();
  await page.getByRole("button", { name: "保存草稿", exact: true }).click();
  expect(control.draft().items[0].owner_results).toEqual({});
});

for (const status of ["已完成", "未完成"]) {
  test(`全部人员选择${status}时自动归为统一结果`, async ({ page }) => {
    const control = await fixture(page),
      dialog = await openPartial(page, control.row);
    for (const person of control.people)
      await dialog
        .getByRole("radiogroup", {
          name: `${person.name}的完成结果`,
          exact: true,
        })
        .getByRole("radio", { name: status, exact: true })
        .press("Space");
    await dialog.getByRole("button", { name: "确认结果", exact: true }).click();
    await expect(
      control.row.locator(".review-result .el-select"),
    ).toContainText(status);
    await page.getByRole("button", { name: "保存草稿", exact: true }).click();
    expect(control.draft().items[0]).toMatchObject({
      status,
      owner_results: {},
    });
  });
}

test("已发布只读会议可查看逐人结果但不能修改", async ({ page }) => {
  const control = await fixture(page, "read");
  await control.row
    .getByRole("button", { name: "查看结果", exact: true })
    .click();
  const dialog = page.getByRole("dialog", {
    name: "按责任人评定",
    exact: true,
  });
  await expect(
    dialog.getByRole("button", { name: "确认结果", exact: true }),
  ).toHaveCount(0);
  await expect(dialog.getByRole("radio").first()).toBeDisabled();
  expect(control.writes).toEqual([]);
});

test("单人任务保留原有五种统一结果", async ({ page }) => {
  const { row } = await fixture(page, "single");
  await row.locator(".review-result .el-select").click();
  await expect(
    page.getByRole("option", { name: "部分完成", exact: true }),
  ).toHaveCount(0);
});
