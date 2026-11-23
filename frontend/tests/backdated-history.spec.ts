import { expect, test, type Page } from "@playwright/test";
import path from "node:path";
import { fileURLToPath } from "node:url";
const evidence = fileURLToPath(
  new URL(
    "../../../.validation/backdated-meeting-link-20261111",
    import.meta.url,
  ),
);

async function fixture(page: Page, admin = true) {
  const host = {
    id: "history-host",
    name: "测试主持人",
    active: true,
    is_admin: admin,
    department_id: "history-dep",
  };
  const series = { id: "history-series", name: "历史衔接测试系列" };
  const item = (id: string, content: string, section = "new") => ({
    id,
    section,
    content,
    category: "",
    requirements: "",
    owner_ids: [host.id],
    due_date: "2026-10-30",
    status: "未到期",
    verifier_id: host.id,
    deliverable: "",
    notes: "",
    task_id: null,
    task_version: null,
    exclusion_reason: "",
  });
  const draft = {
    title: "本次会议草稿",
    kind: "department",
    department_id: host.department_id,
    series_id: series.id,
    host_id: host.id,
    recorder_id: host.id,
    meeting_date: "2026-10-28",
    meeting_time: "",
    location: "",
    attendee_ids: [],
    items: [item("new-item", "本次已填写的新任务")],
    decisions: "",
    absentees: "",
    attachment_ids: [],
    recipient_ids: [],
    cc_ids: [],
  };
  const carryRows = () => [
    {
      ...item("carry-a", "继续推进项目甲", "review"),
      task_id: "task-a",
      task_version: 1,
    },
    {
      ...item("carry-b", "继续推进项目乙", "review"),
      task_id: "task-b",
      task_version: 2,
    },
  ];
  const control = {
    draft: structuredClone(draft),
    version: 1,
    carryRows: [] as any[],
    failCarry: false,
    saved: [] as any[],
    requests: [] as string[],
    carryQuery: "",
    files: [] as string[],
    failOptions: false,
    unexpected: [] as string[],
    errors: [] as string[],
  };
  const record = () => ({
    id: "current-draft",
    draft: control.draft,
    draft_version: control.version,
    published_version: 0,
    can_edit: true,
    has_draft: true,
    historical: !!(control.draft as any).history_backfill,
    versions: [],
    attachments: [],
    pending_edit_requests: [],
  });
  await page.addInitScript(() =>
    sessionStorage.setItem("token", "history-isolated-test"),
  );
  page.on("pageerror", (error) => control.errors.push(error.message));
  await page.route("**/api/**", async (route) => {
    const request = route.request(),
      url = new URL(request.url()),
      path = url.pathname;
    control.requests.push(`${request.method()} ${path}`);
    let response: any,
      status = 200;
    if (path === "/api/me") response = host;
    else if (path === "/api/meeting-people")
      response = {
        users: [host],
        departments: [{ id: host.department_id, name: "测试部门" }],
        series: [series],
      };
    else if (path === "/api/auth/options") response = { wecom: false };
    else if (path === "/api/directory")
      response = {
        users: [host],
        departments: [{ id: host.department_id, name: "测试部门" }],
        series: [series],
      };
    else if (path === "/api/meetings/current-draft") {
      if (request.method() === "PUT") {
        control.draft = request.postDataJSON().draft;
        control.version++;
        control.saved.push(structuredClone(control.draft));
      }
      response = record();
    } else if (path === "/api/backfill-task-options") {
      status = control.failOptions ? 503 : 200;
      response = control.failOptions
        ? { detail: "读取暂时失败" }
        : {
            items: [
              {
                meeting_id: "later-meeting",
                version: 1,
                item_id: "later-row",
                task_id: "later-task",
                meeting_title: "11月11日已发布会议",
                meeting_date: "2026-11-11",
                content: "已发布任务内容",
                owner_names: [host.name],
                due_date: "2026-11-30",
                section: "new",
                status: "未到期",
              },
            ],
          };
    } else if (path === `/api/series/${series.id}/carry-forward`) {
      control.carryQuery = url.search;
      if (control.failCarry) {
        status = 503;
        response = { detail: "读取暂时失败" };
      } else response = control.carryRows;
    } else if (path === "/api/files" && request.method() === "POST") {
      control.files.push(url.searchParams.get("meeting_id") || "");
      response = { id: "supporting-file", filename: "会议补充资料.txt" };
    } else {
      control.unexpected.push(`${request.method()} ${path}`);
      status = 500;
      response = { detail: "Unexpected isolated request" };
    }
    await route.fulfill({ status, json: response });
  });
  return { control, carryRows };
}

for (const width of [1366, 390]) {
  test(`历史补录逐条关联并保留原文 ${width}`, async ({ page }) => {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 900 });
    const { control } = await fixture(page);
    await page.goto("/meetings/current-draft");
    await page.getByText("补录历史会议", { exact: true }).click();
    await page
      .getByRole("button", { name: "新增历史事项", exact: true })
      .click();
    await page
      .getByRole("textbox", { name: "上期工作内容", exact: true })
      .fill("历史填写内容保持不变");
    const row = page
      .locator("tr")
      .filter({
        has: page.getByRole("textbox", { name: "上期工作内容", exact: true }),
      });
    await row
      .getByRole("button", { name: "关联已发布任务", exact: true })
      .click();
    const dialog = page.getByRole("dialog", { name: "关联后续已发布任务" });
    await expect(
      dialog.getByText("已发布任务内容", { exact: true }),
    ).toBeVisible();
    await dialog
      .getByRole("textbox", { name: "搜索已发布任务" })
      .fill("已发布");
    await dialog.getByRole("button", { name: "查询", exact: true }).click();
    await expect(
      dialog.getByRole("button", { name: "确认关联" }),
    ).toBeVisible();
    await page.screenshot({
      path: path.join(evidence, `link-dialog-${width}.png`),
    });
    await expect(page.locator("body")).toHaveJSProperty("scrollWidth", width);
    await dialog.getByRole("button", { name: "确认关联" }).click();
    await expect(
      row.getByRole("textbox", { name: "上期工作内容", exact: true }),
    ).toHaveValue("历史填写内容保持不变");
    await expect(row.getByRole("link")).toContainText("11月11日已发布会议");
    await expect
      .poll(
        () =>
          (control.draft as any).items.find((x: any) => x.section === "review")
            ?.backfill_reference?.task_id,
      )
      .toBe("later-task");
    await page.reload();
    await expect(page.getByRole("link", { name: /已关联/ })).toBeVisible();
    await page.screenshot({
      path: path.join(evidence, `editor-${width}.png`),
      fullPage: true,
    });
    await page.getByRole("button", { name: "取消关联", exact: true }).click();
    await expect(page.getByRole("link", { name: /已关联/ })).toHaveCount(0);
    expect(control.errors).toEqual([]);
    expect(control.unexpected).toEqual([]);
  });
}

test("关联读取失败可重试，不会写入错误关联", async ({ page }) => {
  const { control } = await fixture(page);
  await page.goto("/meetings/current-draft");
  await page.getByText("补录历史会议", { exact: true }).click();
  control.failOptions = true;
  await page
    .getByRole("button", { name: "关联已发布任务", exact: true })
    .click();
  const dialog = page.getByRole("dialog", { name: "关联后续已发布任务" });
  await expect(dialog.getByText("读取暂时失败")).toBeVisible();
  control.failOptions = false;
  await dialog.getByRole("button", { name: "查询", exact: true }).click();
  await expect(dialog.getByRole("button", { name: "确认关联" })).toBeVisible();
  await dialog.getByRole("button", { name: "关闭", exact: true }).click();
  expect((control.draft as any).items[0].backfill_reference).toBeFalsy();
});
