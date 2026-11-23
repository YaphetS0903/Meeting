import { expect, test, type Page } from "@playwright/test";
import path from "node:path";
import { fileURLToPath } from "node:url";
const evidence = fileURLToPath(
  new URL("../../../.validation/closed-carry-20261028", import.meta.url),
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

function reviewSection(page: Page) {
  return page
    .locator("section")
    .filter({
      has: page.getByRole("heading", { name: "上期会议任务跟踪情况" }),
    })
    .last();
}

test("带入仅保留仍未到期的结果，其他完成结果保持请选择", async ({ page }) => {
  const { control, carryRows } = await fixture(page);
  const base = carryRows()[0];
  control.carryRows = [
    base,
    { ...base, id: "same-day", task_id: "same-day", due_date: "2026-10-28" },
    { ...base, id: "expired", task_id: "expired", due_date: "2026-10-27" },
    { ...base, id: "failed", task_id: "failed", status: "未完成" },
  ];
  await page.goto("/meetings/current-draft");
  await page.getByRole("button", { name: "带入上期任务", exact: true }).click();
  const rows = reviewSection(page).locator("tbody tr");
  await expect(rows).toHaveCount(4);
  for (const i of [0, 1]) {
    await expect(rows.nth(i).locator(".review-result")).toHaveAttribute(
      "data-result",
      "未到期",
    );
    await expect(rows.nth(i).locator(".el-select")).toContainText("未到期");
  }
  for (const i of [2, 3]) {
    await expect(rows.nth(i).locator(".review-result")).toHaveAttribute(
      "data-result",
      "",
    );
    await expect(rows.nth(i).locator(".el-select")).toContainText("请选择");
  }
  await page.getByRole("button", { name: "保存草稿", exact: true }).click();
  expect(
    control.draft.items
      .filter((row: any) => row.section === "review")
      .map((row: any) => row.status),
  ).toEqual(["未到期", "未到期", "", ""]);
  await page.reload();
  await expect(rows.first().locator(".el-select")).toContainText("未到期");
  expect(control.errors).toEqual([]);
  expect(control.unexpected).toEqual([]);
});

test("持续新建默认的未到期结果可以改为手动评定并保留", async ({ page }) => {
  const { control, carryRows } = await fixture(page);
  control.draft.items = [
    {
      ...carryRows()[0],
      source: { continuation_default_status: "未到期" },
    } as any,
  ];
  await page.goto("/meetings/current-draft");
  const result = reviewSection(page)
    .locator("tbody tr")
    .first()
    .locator(".el-select");
  await expect(result).toContainText("未到期");
  await result.click();
  await page.getByRole("option", { name: "未完成", exact: true }).click();
  await page.getByRole("button", { name: "保存草稿", exact: true }).click();
  const item = control.draft.items[0] as any;
  expect(item.status).toBe("未完成");
  expect(item.source.continuation_default_status).toBeUndefined();
  await page.reload();
  await expect(result).toContainText("未完成");
  expect(control.errors).toEqual([]);
  expect(control.unexpected).toEqual([]);
});

test("会议编辑保留带入入口和普通附件上传，不再显示历史导入入口", async ({
  page,
}) => {
  const { control } = await fixture(page);
  await page.goto("/meetings/current-draft");
  const review = reviewSection(page);
  await expect(
    review.getByRole("button", { name: "一键排序", exact: true }),
  ).toBeVisible();
  await expect(
    review.getByRole("button", { name: "带入上期任务", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "选择已入库任务单", exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "上传上期任务单", exact: true }),
  ).toHaveCount(0);
  await expect(page.getByRole("link", { name: "导入历史会议" })).toHaveCount(0);
  await page.getByRole("button", { name: "带入上期任务", exact: true }).click();
  await expect(page.locator(".review-source-message")).toHaveText(
    "该系列暂无需要继续跟进的上期任务。",
  );
  await expect(review).not.toContainText("上传历史任务单");
  await page.screenshot({
    path: path.join(evidence, "carry-desktop.png"),
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(
    review.getByRole("button", { name: "带入上期任务", exact: true }),
  ).toBeVisible();
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth),
  ).toBeLessThanOrEqual(390);
  await page.screenshot({
    path: path.join(evidence, "carry-mobile.png"),
    fullPage: true,
  });
  await page.locator('input[type="file"]').setInputFiles({
    name: "会议补充资料.txt",
    mimeType: "text/plain",
    buffer: Buffer.from("supporting document"),
  });
  await expect(
    page.getByRole("button", { name: "会议补充资料.txt", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "保存草稿", exact: true }).click();
  expect(control.files).toEqual(["current-draft"]);
  expect(control.draft.attachment_ids).toEqual(["supporting-file"]);
  expect(control.draft.items).toHaveLength(1);
  expect(control.errors).toEqual([]);
  expect(control.unexpected).toEqual([]);
});

test("带入仍需跟进的任务不重复，保留本次内容与已填写备注", async ({ page }) => {
  const { control, carryRows } = await fixture(page);
  control.carryRows = carryRows();
  await page.goto("/meetings/current-draft");
  await page.getByRole("button", { name: "带入上期任务", exact: true }).click();
  const review = reviewSection(page);
  await expect(review.locator("tbody tr")).toHaveCount(2);
  await expect(
    review.getByText("继续推进项目甲", { exact: true }),
  ).toBeVisible();
  await review.locator("textarea").first().fill("保留本次人工填写的备注");
  await page.getByRole("button", { name: "带入上期任务", exact: true }).click();
  await expect(page.locator(".review-source-message")).toContainText(
    "无需重复添加",
  );
  await expect(review.locator("tbody tr")).toHaveCount(2);
  await page.getByRole("button", { name: "保存草稿", exact: true }).click();
  expect(
    control.draft.items.find((row: any) => row.task_id === "task-a")?.notes,
  ).toBe("保留本次人工填写的备注");
  expect(
    control.draft.items.find((row: any) => row.id === "new-item")?.content,
  ).toBe("本次已填写的新任务");
  expect(control.carryQuery).toContain("meeting_date=2026-10-28");
  expect(control.carryQuery).toContain("exclude_meeting_id=current-draft");
  expect(control.errors).toEqual([]);
  expect(control.unexpected).toEqual([]);
});

test("带入失败可重试，普通账号同样不出现已删除模块", async ({ page }) => {
  const { control } = await fixture(page, false);
  control.failCarry = true;
  await page.goto("/meetings/current-draft");
  await expect(
    page.getByRole("button", { name: "选择已入库任务单", exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "上传上期任务单", exact: true }),
  ).toHaveCount(0);
  await page.getByRole("button", { name: "带入上期任务", exact: true }).click();
  await expect(page.locator(".review-source-message")).toContainText(
    "加载失败",
  );
  control.failCarry = false;
  await page.getByRole("button", { name: "带入上期任务", exact: true }).click();
  await expect(page.locator(".review-source-message")).toHaveText(
    "该系列暂无需要继续跟进的上期任务。",
  );
  expect(control.errors).toEqual([]);
  expect(control.unexpected).toEqual([]);
});

test("旧完整任务单回跳链接不再读取或修改本次草稿", async ({ page }) => {
  const { control } = await fixture(page);
  await page.goto(
    "/meetings/current-draft?import_history=retired-template&history_version=1",
  );
  await expect(
    page.getByRole("heading", { name: "在线会议编辑" }),
  ).toBeVisible();
  await expect(
    page.getByRole("textbox", { name: "工作内容及要求" }).first(),
  ).toHaveValue("本次已填写的新任务");
  await expect(
    page.getByRole("dialog", { name: "选择已入库任务单" }),
  ).toHaveCount(0);
  expect(
    control.requests.some((request) =>
      /history-templates|import-history-template/.test(request),
    ),
  ).toBe(false);
  expect(control.saved).toEqual([]);
  expect(control.draft.items).toHaveLength(1);
  expect(control.errors).toEqual([]);
  expect(control.unexpected).toEqual([]);
});

test("接续新建仍自动带入，完成后清理 carry 参数", async ({ page }) => {
  const { control, carryRows } = await fixture(page);
  control.carryRows = carryRows();
  await page.goto(
    "/meetings/current-draft?carry=1&series=series:history-series",
  );
  await expect(
    reviewSection(page).getByText("继续推进项目甲", { exact: true }),
  ).toBeVisible();
  await expect(page).toHaveURL(
    /meetings\/current-draft\?series=series:history-series/,
  );
  await expect(reviewSection(page).locator("tbody tr")).toHaveCount(2);
  expect(control.errors).toEqual([]);
  expect(control.unexpected).toEqual([]);
});
