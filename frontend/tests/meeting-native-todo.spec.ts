import { expect, test, type Locator, type Page } from "@playwright/test";
import path from "node:path";

const meetingId = "isolated-meeting-native";
const nativeDialogTitle = "派发本次会议的企微待办";

function publicationPdf() {
  const stream =
    "BT /F1 18 Tf 72 740 Td (Meeting publication preview) Tj 0 -32 Td /F1 12 Tf (Isolated UI validation - no messages sent) Tj ET";
  const objects = [
    "<< /Type /Catalog /Pages 2 0 R >>",
    "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
    "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
    "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    `<< /Length ${Buffer.byteLength(stream, "ascii")} >>\nstream\n${stream}\nendstream`,
  ];
  let source = "%PDF-1.4\n";
  const offsets = [0];
  for (const [index, object] of objects.entries()) {
    offsets.push(Buffer.byteLength(source, "ascii"));
    source += `${index + 1} 0 obj\n${object}\nendobj\n`;
  }
  const xrefOffset = Buffer.byteLength(source, "ascii");
  source += `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n`;
  source += offsets
    .slice(1)
    .map((offset) => `${String(offset).padStart(10, "0")} 00000 n \n`)
    .join("");
  source += `trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${xrefOffset}\n%%EOF\n`;
  return Buffer.from(source, "ascii");
}

async function settledDialog(dialog: Locator) {
  await expect(dialog).toBeVisible();
  await expect
    .poll(() =>
      dialog.evaluate((element) => {
        let current: Element | null = element;
        while (current) {
          const style = getComputedStyle(current);
          if (style.opacity !== "1" || style.transform !== "none") return false;
          current = current.parentElement;
        }
        return true;
      }),
    )
    .toBe(true);
}

async function disabledDispatchAppearance(dialog: Locator) {
  const dispatch = dialog.getByRole("button", {
    name: "确认派发全部待办",
    exact: true,
  });
  await expect(dispatch).toBeDisabled();
  await expect(dispatch).toHaveCSS("background-image", "none");
  await expect(dispatch).toHaveCSS("box-shadow", "none");
  await expect(dispatch).toHaveCSS("transform", "none");
}

async function mockMeeting(
  page: Page,
  options: {
    published?: boolean;
    publishedVersion?: number;
    planFails?: boolean;
    existingLink?: boolean;
    nativeClient?: boolean;
    attachments?: any[];
  } = {},
) {
  if (options.nativeClient)
    await page.route("**/src/wecom.ts*", (route) =>
      route.fulfill({
        contentType: "application/javascript",
        body: `
          export const insideWeCom = () => true;
          export const ensureWeComTodo = async () => {};
          export async function createWeComTodo(payload) {
            window.__isolatedNativeCalls = [...(window.__isolatedNativeCalls || []), payload.content];
            if (payload.content.includes("第二项取消")) throw {errMsg: "createTodo:cancel"};
            return {todoId: "isolated-native-" + window.__isolatedNativeCalls.length};
          }
          export const viewWeComTodo = async () => {};
          export const nativeError = error => error?.message || error?.errMsg || "隔离企微错误";
        `,
      }),
    );
  const user = {
    id: "isolated-meeting-host",
    name: "隔离会议主持人",
    active: true,
    is_admin: false,
    can_assign_tasks: false,
    can_oversee_tasks: false,
    department_id: "isolated-meeting-department",
    position: "工程师",
    wecom_id: "isolated-host-wecom",
    email: "isolated-host@example.test",
  };
  const owner = {
    ...user,
    id: "isolated-meeting-owner",
    name: "隔离会议责任人",
    wecom_id: "isolated-owner-wecom",
    email: "isolated-owner@example.test",
  };
  const draft = {
    title: "隔离会议原生待办验证",
    kind: "department",
    department_id: user.department_id,
    series_id: null,
    meeting_date: "2026-10-26",
    meeting_time: "14:00",
    location: "隔离会议室",
    host_id: user.id,
    recorder_id: user.id,
    reviewer_id: null,
    attendee_ids: [user.id, owner.id],
    absentees: "",
    decisions: "",
    recipient_ids: [],
    cc_ids: [],
    attachment_ids: [],
    items: [
      {
        id: "isolated-arrangement",
        section: "new",
        category: "验证",
        content: "检查会议任务的责任人待办",
        owner_ids: [owner.id],
        owner_names: [owner.name],
        verifier_id: user.id,
        due_date: "2026-10-30",
        requirements: "",
        deliverable: "验证结果",
        notes: "",
        status: "未到期",
        task_id: "isolated-meeting-task",
        task_version: 1,
      },
    ],
  };
  let published = !!options.published;
  let draftVersion = 3;
  let stored = structuredClone(draft);
  const controls = {
    planFails: !!options.planFails,
    publishes: [] as any[],
    planReads: 0,
    nativeWrites: [] as string[],
    unexpected: [] as string[],
    pageErrors: [] as string[],
  };
  const nativeState = {
    enabled: true,
    can_create: !options.existingLink,
    is_issuer: true,
    source: "meeting",
    reason: "",
    pending_attempt: null,
    link: options.existingLink
      ? { provider: "app", todo_id: "isolated-linked-todo", state: "active" }
      : null,
    warning: "",
    message_state: "skipped",
  };
  const meeting = () => ({
    id: meetingId,
    title: stored.title,
    series_id: null,
    historical: false,
    draft: stored,
    draft_version: draftVersion,
    published_version: published ? options.publishedVersion || 1 : 0,
    has_draft: !published,
    can_edit: true,
    can_delete: false,
    can_continue: false,
    versions: published
      ? [
          { number: 1, snapshot: stored },
          ...(options.publishedVersion
            ? [{ number: options.publishedVersion, snapshot: stored }]
            : []),
        ]
      : [],
    attachments: options.attachments || [],
    notifications: [],
    task_notifications: [],
    assessments: {},
  });
  page.on("pageerror", (error) => controls.pageErrors.push(error.message));
  await page.addInitScript(() => {
    if (window === window.top)
      sessionStorage.setItem("token", "isolated-meeting-native-token");
  });
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    let body: any;
    let status = 200;
    if (path === "/api/me") body = user;
    else if (path === "/api/auth/options") body = { wecom: false };
    else if (path === "/api/directory")
      body = {
        users: [user, owner],
        departments: [{ id: user.department_id, name: "隔离验证部门" }],
        series: [],
      };
    else if (path === "/api/meeting-people") body = { users: [user, owner] };
    else if (path === `/api/meetings/${meetingId}/preview`) {
      await route.fulfill({
        status: 200,
        contentType: "application/pdf",
        body: publicationPdf(),
      });
      return;
    } else if (path === `/api/meetings/${meetingId}/publish`) {
      controls.publishes.push(request.postDataJSON());
      published = true;
      body = { published_version: 1 };
    } else if (path === `/api/meetings/${meetingId}/native-todos`) {
      controls.planReads += 1;
      if (controls.planFails) {
        status = 503;
        body = { detail: "隔离待办计划暂时不可用" };
      } else
        body = {
          meeting_id: meetingId,
          published_version: 1,
          enabled: true,
          can_confirm: true,
          reason: "",
          tasks: [
            {
              id: "isolated-meeting-task",
              code: "RW-ISOLATED-MEETING",
              content: draft.items[0].content,
              due_date: draft.items[0].due_date,
              version: 1,
              owner_names: [owner.name],
              source_meeting_id: meetingId,
              native_todo: nativeState,
            },
          ],
          excluded_tasks: [
            {
              id: "isolated-external-task",
              content: "跨会议引用事项保持原权限",
              owner_names: [owner.name],
              reason: "当前账号无原任务所属会议的管理权限",
            },
          ],
        };
    } else if (path === "/api/tasks/isolated-meeting-task/native-todo")
      body = nativeState;
    else if (path.startsWith("/api/tasks/") && request.method() !== "GET") {
      controls.nativeWrites.push(`${request.method()} ${path}`);
      status = 400;
      body = { detail: "普通浏览器不应尝试创建原生待办" };
    } else if (path === `/api/meetings/${meetingId}`) {
      if (request.method() === "PUT") {
        stored = request.postDataJSON().draft;
        draftVersion += 1;
      }
      body = meeting();
    } else {
      controls.unexpected.push(`${request.method()} ${path}`);
      status = 404;
      body = { detail: "未预期的隔离请求" };
    }
    await route.fulfill({
      status,
      contentType: "application/json",
      body: JSON.stringify(body),
    });
  });
  await page.goto(`/meetings/${meetingId}`);
  await expect(
    page.getByRole("heading", { name: "会议基本信息", exact: true }),
  ).toBeVisible();
  return controls;
}

async function openPublication(page: Page) {
  await page.getByRole("button", { name: "预览并发布", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "确认发布会议纪要" });
  await expect(
    dialog.getByRole("region", { name: "发布前 PDF 纪要预览" }),
  ).toBeVisible();
  await expect(
    dialog.getByRole("button", { name: "确认发布", exact: true }),
  ).toBeEnabled();
  return dialog;
}

for (const mobile of [false, true]) {
  test(`导航一键收起可恢复并记忆，附件仅保留最新且无版本号（${mobile ? "手机" : "桌面"}）`, async ({
    page,
  }) => {
    await page.setViewportSize(
      mobile ? { width: 390, height: 844 } : { width: 1440, height: 1000 },
    );
    await mockMeeting(page, {
      published: true,
      publishedVersion: 2,
      attachments: [
        {
          id: "upload",
          filename: "上传原始附件.xlsx",
          kind: "attachment",
          meeting_version: null,
        },
        {
          id: "current",
          filename: "隔离会议原生待办验证-V2-纪要.png",
          kind: "minutes_png",
          meeting_version: 2,
        },
        {
          id: "current-xlsx",
          filename: "隔离会议原生待办验证-V2-纪要.xlsx",
          kind: "minutes_xlsx",
          meeting_version: 2,
        },
        {
          id: "current-tasks",
          filename: "隔离会议原生待办验证-V2-任务单.xlsx",
          kind: "tasks_xlsx",
          meeting_version: 2,
        },
        {
          id: "current-pdf",
          filename: "隔离会议原生待办验证-V2-纪要.pdf",
          kind: "minutes_pdf",
          meeting_version: 2,
        },
        {
          id: "old",
          filename: "历史纪要.pdf",
          kind: "minutes_pdf",
          meeting_version: 1,
        },
      ],
    });
    const attachments = page.locator("section").filter({
      has: page.getByRole("heading", { name: "会议附件", exact: true }),
    });
    await expect(
      attachments.getByRole("button", {
        name: "隔离会议原生待办验证-纪要.xlsx",
        exact: true,
      }),
    ).toBeVisible();
    await expect(
      attachments.getByRole("button", {
        name: "上传原始附件.xlsx",
        exact: true,
      }),
    ).toBeVisible();
    await expect(
      attachments.getByRole("button", { name: "历史纪要.pdf", exact: true }),
    ).toHaveCount(0);
    await expect(attachments.locator("summary")).toHaveCount(0);
    await expect(attachments.getByText(/-V\d+-/)).toHaveCount(0);
    await expect(attachments.getByRole("button")).toHaveCount(3);
    await expect(
      attachments.getByRole("button", { name: /任务单\.xlsx|纪要\.png/ }),
    ).toHaveCount(0);
    await expect(
      page.getByRole("button", { name: "Excel 任务单", exact: true }),
    ).toHaveCount(0);
    await expect(
      page.getByRole("button", { name: "Excel 纪要", exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: "PDF 纪要", exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole("combobox", { name: "查看版本", exact: true }),
    ).toHaveCount(0);
    await expect(
      attachments.getByRole("button", {
        name: "隔离会议原生待办验证-纪要.pdf",
        exact: true,
      }),
    ).toBeVisible();
    await expect(
      attachments.getByRole("button", { name: "历史纪要.pdf", exact: true }),
    ).toHaveCount(0);
    const toggle = page.locator(".navigation-trigger");
    await expect(toggle).toHaveAttribute("aria-expanded", "false");
    if (mobile) {
      await expect(page.locator(".sidebar")).toHaveCount(0);
      await expect(page.locator(".workspace")).toHaveCSS("margin-left", "0px");
    } else {
      await expect(page.locator(".sidebar")).toHaveCSS("width", "64px");
      await expect(page.locator(".workspace")).toHaveCSS("margin-left", "64px");
    }
    await toggle.focus();
    await toggle.press("Enter");
    await expect(
      page.getByRole("link", { name: "我的待办", exact: true }),
    ).toBeVisible();
    if (mobile) {
      await page.keyboard.press("Escape");
      await expect(page.locator(".navigation-drawer")).toBeHidden();
      await expect(toggle).toBeFocused();
    } else {
      await expect(page.locator(".sidebar")).toHaveCSS("width", "224px");
      await toggle.click();
      await page.reload();
      await expect(page.locator(".sidebar")).toHaveCSS("width", "64px");
    }
    await page
      .getByRole("heading", { name: "会议附件", exact: true })
      .scrollIntoViewIfNeeded();
    await page.screenshot({
      path: path.resolve(
        "../../.validation/meeting-attachment-downloads-20261105",
        `${mobile ? "mobile" : "desktop"}-attachments.png`,
      ),
      animations: "disabled",
    });
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({
      path: path.resolve(
        "../../.validation/meeting-attachment-downloads-20261105",
        `${mobile ? "mobile" : "desktop"}-expanded.png`,
      ),
      animations: "disabled",
    });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth > innerWidth,
      ),
    ).toBe(false);
  });
  test(`发布只显示简短提示，手动派发入口保留（${mobile ? "手机" : "桌面"}）`, async ({
    page,
  }) => {
    await page.setViewportSize(
      mobile ? { width: 390, height: 844 } : { width: 1440, height: 1000 },
    );
    const controls = await mockMeeting(page);
    const publication = await openPublication(page);
    await expect(
      publication.getByText("发布后派发企微待办", { exact: true }),
    ).toHaveCount(0);
    await publication
      .getByRole("button", { name: "确认发布", exact: true })
      .click();
    await expect(page.getByText("发布成功", { exact: true })).toBeVisible();
    await expect(
      page.getByRole("dialog", { name: nativeDialogTitle }),
    ).toHaveCount(0);
    expect(controls.planReads).toBe(0);
    expect(controls.nativeWrites).toEqual([]);
    await page
      .getByRole("button", { name: "派发企微待办", exact: true })
      .click();
    await expect(
      page.getByRole("dialog", { name: nativeDialogTitle }),
    ).toBeVisible();
    expect(controls.publishes).toHaveLength(1);
    expect(controls.pageErrors).toEqual([]);
    expect(controls.unexpected).toEqual([]);
  });
}

test("发布成功后待办计划加载失败可独立重试，不重复发布会议", async ({
  page,
}) => {
  const controls = await mockMeeting(page, { planFails: true });
  const publication = await openPublication(page);
  await publication
    .getByRole("button", { name: "确认发布", exact: true })
    .click();
  await page.getByRole("button", { name: "派发企微待办", exact: true }).click();
  const nativeDialog = page.getByRole("dialog", { name: nativeDialogTitle });
  await expect(nativeDialog).toBeVisible();
  await expect(nativeDialog).toContainText("隔离待办计划暂时不可用");
  await expect(
    page.getByRole("button", { name: "派发企微待办", exact: true }),
  ).toBeVisible();
  expect(controls.publishes).toHaveLength(1);
  controls.planFails = false;
  await nativeDialog
    .getByRole("button", { name: /重试|重新加载|刷新/ })
    .click();
  await expect(nativeDialog).toContainText("检查会议任务的责任人待办");
  await expect(nativeDialog).not.toContainText("隔离待办计划暂时不可用");
  expect(controls.publishes).toHaveLength(1);
  expect(controls.planReads).toBeGreaterThanOrEqual(2);
  expect(controls.nativeWrites).toEqual([]);
  expect(controls.unexpected).toEqual([]);
  expect(controls.pageErrors).toEqual([]);
});

test("已有待办和跨会议权限阻断逐项展示，普通浏览器不会发起创建", async ({
  page,
}) => {
  const controls = await mockMeeting(page, {
    published: true,
    existingLink: true,
  });
  await page.getByRole("button", { name: "派发企微待办", exact: true }).click();
  const nativeDialog = page.getByRole("dialog", { name: nativeDialogTitle });
  await expect(nativeDialog).toBeVisible();
  await expect(nativeDialog).toContainText("检查会议任务的责任人待办");
  await expect(nativeDialog).toContainText(/已关联|已创建|已有待办|不重复/);
  await expect(nativeDialog).toContainText("跨会议引用事项保持原权限");
  await expect(nativeDialog).toContainText(
    "当前账号无原任务所属会议的管理权限",
  );
  await expect(
    nativeDialog.getByRole("button", { name: "确认派发全部待办", exact: true }),
  ).toBeDisabled();
  expect(controls.publishes).toHaveLength(0);
  expect(controls.nativeWrites).toEqual([]);
  expect(controls.unexpected).toEqual([]);
  expect(controls.pageErrors).toEqual([]);
});

test("企微内发布沿用旧待办时显示保留结果，不重复唤起原生创建", async ({
  page,
}) => {
  const controls = await mockMeeting(page, {
    existingLink: true,
    nativeClient: true,
  });
  const publication = await openPublication(page);
  await publication
    .getByRole("button", { name: "确认发布", exact: true })
    .click();
  await page.getByRole("button", { name: "派发企微待办", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: nativeDialogTitle });
  await expect(dialog).toBeVisible();
  await expect(dialog).toContainText(/已有|已关联|已创建/);
  await expect(
    dialog.getByRole("button", { name: "确认派发全部待办", exact: true }),
  ).toBeDisabled();
  await expect.poll(() => controls.planReads).toBeGreaterThanOrEqual(1);
  expect(controls.publishes).toHaveLength(1);
  expect(controls.publishes[0].notify).toBe(true);
  expect(controls.nativeWrites).toEqual([]);
  expect(
    await page.evaluate(() => (window as any).__isolatedNativeCalls || []),
  ).toEqual([]);
  expect(controls.unexpected).toEqual([]);
  expect(controls.pageErrors).toEqual([]);
  await settledDialog(dialog);
  await page.screenshot({
    animations: "disabled",
    path: path.resolve(
      ".validation/meeting-publication-notice-20261027/existing-native-retained.png",
    ),
  });
});

test("批量企微确认串行执行，用户取消第二项后暂停且不创建第三项", async ({
  page,
}) => {
  const controls = await mockMeeting(page, {
    published: true,
    nativeClient: true,
  });
  const taskNames = ["第一项创建成功", "第二项取消", "第三项不得继续"];
  const taskStates = taskNames.map(() => ({
    enabled: true,
    can_create: true,
    is_issuer: true,
    source: "meeting",
    reason: "",
    link: null as any,
    pending_attempt: null,
    warning: "",
    message_state: null,
  }));
  const operations: string[] = [];
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    let body: any;
    if (path === `/api/meetings/${meetingId}/native-todos`)
      body = {
        meeting_id: meetingId,
        published_version: 1,
        enabled: true,
        can_confirm: true,
        reason: "",
        excluded_tasks: [],
        tasks: taskNames.map((name, index) => ({
          id: `isolated-batch-${index}`,
          code: `RW-ISOLATED-BATCH-${index}`,
          content: name,
          due_date: "2026-10-30",
          version: 1,
          owner_names: ["隔离会议责任人"],
          source_meeting_id: meetingId,
          native_todo: taskStates[index],
        })),
      };
    else {
      const match = path.match(
        /^\/api\/tasks\/isolated-batch-(\d)\/native-todo(?:\/(prepare|record|bind|reset))?$/,
      );
      if (!match) {
        await route.fallback();
        return;
      }
      const index = Number(match[1]);
      const action = match[2] || "state";
      operations.push(`${index}:${action}`);
      if (action === "prepare")
        body = {
          attempt_id: `isolated-attempt-${index}`,
          payload: {
            content: taskNames[index],
            attendees: ["isolated-owner-wecom"],
          },
        };
      else {
        if (action === "bind") {
          taskStates[index].link = {
            provider: "app",
            todo_id: request.postDataJSON().todo_id,
            state: "active",
          };
          taskStates[index].can_create = false;
        }
        body = taskStates[index];
      }
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(body),
    });
  });
  await page.getByRole("button", { name: "派发企微待办", exact: true }).click();
  const nativeDialog = page.getByRole("dialog", { name: nativeDialogTitle });
  await nativeDialog
    .getByRole("button", { name: "确认派发全部待办", exact: true })
    .click();
  await expect(nativeDialog).toContainText(/取消|暂停/);
  await expect.poll(() => operations.includes("1:reset")).toBe(true);
  expect(operations.filter((value) => value.endsWith(":prepare"))).toEqual([
    "0:prepare",
    "1:prepare",
  ]);
  expect(operations).toContain("0:bind");
  expect(operations).not.toContain("2:prepare");
  expect(
    await page.evaluate(() => (window as any).__isolatedNativeCalls),
  ).toEqual(["第一项创建成功", "第二项取消"]);
  expect(controls.publishes).toHaveLength(0);
  expect(controls.unexpected).toEqual([]);
  expect(controls.pageErrors).toEqual([]);
});
