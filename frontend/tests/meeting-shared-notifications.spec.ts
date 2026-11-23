import { expect, test, type Locator, type Page } from "@playwright/test";
import path from "node:path";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const meetingId = "isolated-shared-notifications";
const title = "确认发布会议纪要";
const output = path.resolve("../../.validation/directory-mail-20261028");

async function fixture(
  page: Page,
  recipients: string[] = [],
  attendees: string[] = ["host"],
) {
  const base = {
    active: true,
    is_admin: false,
    department_id: "dept",
    department_ids: ["dept"],
    can_assign_tasks: false,
  };
  const host = {
    ...base,
    id: "host",
    name: "隔离主持人",
    email: "host@example.test",
    wecom_id: "wecom-host",
  };
  const owner = {
    ...base,
    id: "owner",
    name: "隔离责任人",
    email: "owner@example.test",
    wecom_id: "wecom-owner",
  };
  const second = {
    ...base,
    id: "second",
    name: "第二责任人",
    email: "second@example.test",
    wecom_id: "wecom-second",
  };
  const cc = {
    ...base,
    id: "cc",
    name: "仅邮件同事",
    email: "cc@example.test",
    wecom_id: null,
  };
  const incomplete = {
    ...base,
    id: "incomplete",
    name: "未配置管理员",
    is_admin: true,
    email: null,
    wecom_id: null,
  };
  const outsider = {
    ...base,
    id: "outsider",
    name: "无关部门人员",
    department_id: "other",
    department_ids: ["other"],
    email: "other@example.test",
    wecom_id: "wecom-other",
  };
  let draft: any = {
    title: "隔离通知名单会议",
    department_id: "dept",
    series_id: null,
    kind: "department",
    meeting_date: "2026-10-28",
    meeting_time: "10:00",
    location: "隔离会议室",
    host_id: host.id,
    recorder_id: host.id,
    reviewer_id: incomplete.id,
    attendee_ids: attendees,
    absentees: "",
    decisions: "",
    attachment_ids: [],
    recipient_ids: recipients,
    cc_ids: [],
    cc_emails: [],
    items: [
      {
        id: "item",
        section: "new",
        content: "隔离工作",
        category: "",
        requirements: "",
        deliverable: "",
        notes: "",
        owner_ids: [owner.id, second.id],
        due_date: "2026-11-10",
        verifier_id: host.id,
        status: "未到期",
        exclusion_reason: "",
        task_id: null,
        task_version: null,
      },
    ],
  };
  let version = 1,
    published = false;
  const controls = {
    saved: [] as any[],
    publications: [] as any[],
    unexpected: [] as string[],
    errors: [] as string[],
    users: [host, owner, second, cc, incomplete, outsider] as any[],
    companyUsers: [] as any[],
    directoryReads: 0,
    rosterReads: 0,
    supplement: null as any,
  };
  const record = () => ({
    id: meetingId,
    title: draft.title,
    department_id: "dept",
    series_id: null,
    historical: false,
    draft,
    draft_version: version,
    published_version: published ? 1 : 0,
    has_draft: !published,
    can_edit: true,
    can_continue: published,
    can_delete: false,
    versions: published ? [{ number: 1, snapshot: draft }] : [],
    attachments: [],
    notifications: [],
    task_notifications: [],
    assessments: {},
  });
  await page.addInitScript(() => {
    sessionStorage.setItem("token", "isolated-shared-notifications-token");
  });
  page.on("pageerror", (error) => controls.errors.push(error.message));
  await page.route("**/api/**", async (route) => {
    const request = route.request(),
      url = new URL(request.url());
    let body: any;
    if (url.pathname === "/api/me") body = host;
    else if (url.pathname === "/api/auth/options") body = { wecom: false };
    else if (url.pathname === "/api/directory") {
      controls.directoryReads++;
      body = {
        users: controls.users,
        departments: [
          { id: "dept", name: "隔离信息部" },
          { id: "other", name: "其他部门" },
        ],
        series: [],
      };
    } else if (url.pathname === "/api/meeting-people") {
      controls.rosterReads++;
      body = {
        users: [...controls.users, ...controls.companyUsers].filter(
          (user) => user.active,
        ),
      };
    } else if (url.pathname === `/api/meetings/${meetingId}/preview`) {
      await route.fulfill({
        contentType: "application/pdf",
        body: readFileSync(
          fileURLToPath(
            new URL("./fixtures/preview-minutes.pdf", import.meta.url),
          ),
        ),
      });
      return;
    } else if (url.pathname === `/api/meetings/${meetingId}/publish`) {
      controls.publications.push({
        command: request.postDataJSON(),
        draft: structuredClone(draft),
      });
      published = true;
      body = { published_version: 1, history_supplement: controls.supplement };
    } else if (url.pathname === `/api/meetings/${meetingId}/native-todos`) {
      body = {
        enabled: false,
        can_confirm: false,
        tasks: [],
        excluded_tasks: [],
        reason: "隔离测试不派发待办",
      };
    } else if (url.pathname === `/api/meetings/${meetingId}`) {
      if (request.method() === "PUT") {
        draft = request.postDataJSON().draft;
        controls.saved.push(structuredClone(draft));
        version++;
      }
      body = record();
    } else {
      controls.unexpected.push(`${request.method()} ${url.pathname}`);
      await route.fulfill({ status: 404, json: { detail: "隔离路由未实现" } });
      return;
    }
    await route.fulfill({ json: body });
  });
  await page.goto(`/meetings/${meetingId}`);
  await expect(
    page.getByRole("button", { name: "预览并发布", exact: true }),
  ).toBeVisible();
  return controls;
}

async function openPreview(page: Page) {
  await page.getByRole("button", { name: "预览并发布", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: title });
  await expect(
    dialog.getByRole("button", { name: "确认发布", exact: true }),
  ).toBeEnabled();
  return dialog;
}

function selection(dialog: Locator, label: string) {
  return dialog.locator(".el-form-item").filter({
    has: dialog.page().getByRole("combobox", { name: label, exact: true }),
  });
}

async function removePerson(dialog: Locator, name: string) {
  await selection(dialog, "通知接收人")
    .locator(".el-tag")
    .filter({ hasText: name })
    .locator(".el-tag__close")
    .click();
}

for (const width of [1366, 390]) {
  test(`手动删除通知接收人保存刷新后仍保留且邮件同步移除 ${width}`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 900 });
    const controls = await fixture(page, [], ["owner", "second"]);
    let dialog = await openPreview(page);
    await expect(
      selection(dialog, "通知接收人").locator(".el-tag"),
    ).toHaveCount(3);
    await removePerson(dialog, "隔离主持人");
    await removePerson(dialog, "隔离责任人");
    await expect(
      selection(dialog, "通知接收人").locator(".el-tag"),
    ).toHaveCount(1);
    await expect(dialog.locator(".mail-recipient-list")).not.toContainText(
      "host@example.test",
    );
    await expect(dialog.locator(".mail-recipient-list")).not.toContainText(
      "owner@example.test",
    );
    await dialog.getByRole("button", { name: "返回编辑", exact: true }).click();
    await page.getByRole("button", { name: "保存草稿", exact: true }).click();
    await expect
      .poll(() => controls.saved.at(-1)?.recipient_ids)
      .toEqual(["second"]);
    expect(controls.saved.at(-1)?.recipient_customized).toBe(true);
    expect(controls.saved.at(-1)?.attendee_ids).toEqual(["owner", "second"]);
    await page.reload();
    dialog = await openPreview(page);
    await expect(
      selection(dialog, "通知接收人").locator(".el-tag"),
    ).toHaveCount(1);
    await expect(dialog.locator(".mail-recipient-list")).toContainText(
      "second@example.test",
    );
    await expect(dialog.locator(".pdf-preview")).toHaveAttribute(
      "aria-busy",
      "false",
    );
    await selection(dialog, "通知接收人")
      .locator("xpath=..")
      .screenshot({
        path: path.resolve(
          `../../.validation/recipient-removal-20261118/recipient-removed-${width}.png`,
        ),
      });
    await dialog.getByRole("button", { name: "确认发布", exact: true }).click();
    await expect.poll(() => controls.publications.length).toBe(1);
    expect(controls.publications[0].draft.recipient_ids).toEqual(["second"]);
    expect(controls.publications[0].draft.attendee_ids).toEqual([
      "owner",
      "second",
    ]);
    expect(controls.publications[0].draft.cc_ids).toEqual([]);
    expect(controls.errors).toEqual([]);
    expect(controls.unexpected).toEqual([]);
  });

  test(`通知接收人可全部删除并保存，恢复参会名单时才重新加入 ${width}`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 900 });
    const controls = await fixture(page, [], ["owner", "second"]);
    let dialog = await openPreview(page);
    for (const name of ["隔离主持人", "隔离责任人", "第二责任人"])
      await removePerson(dialog, name);
    await expect(
      selection(dialog, "通知接收人").locator(".el-tag"),
    ).toHaveCount(0);
    await expect(
      dialog.getByText("尚未选择通知接收人", { exact: true }),
    ).toBeVisible();
    await dialog.getByRole("button", { name: "返回编辑", exact: true }).click();
    await page.getByRole("button", { name: "保存草稿", exact: true }).click();
    await expect.poll(() => controls.saved.at(-1)?.recipient_ids).toEqual([]);
    await page.reload();
    dialog = await openPreview(page);
    await expect(
      selection(dialog, "通知接收人").locator(".el-tag"),
    ).toHaveCount(0);
    await dialog.getByRole("button", { name: "确认发布", exact: true }).click();
    expect(controls.publications).toEqual([]);
    await dialog
      .getByRole("button", { name: "恢复参会名单（3 人）", exact: true })
      .click();
    await expect(
      selection(dialog, "通知接收人").locator(".el-tag"),
    ).toHaveCount(3);
    await expect(
      dialog.locator(".mail-recipient-list").locator(".el-tag"),
    ).toHaveCount(3);
    await dialog.getByRole("button", { name: "返回编辑", exact: true }).click();
    dialog = await openPreview(page);
    await expect
      .poll(() => controls.saved.at(-1)?.recipient_customized)
      .toBe(false);
    expect(controls.saved.at(-1)?.recipient_ids).toEqual([
      "host",
      "owner",
      "second",
    ]);
    expect(controls.errors).toEqual([]);
    expect(controls.unexpected).toEqual([]);
  });
}

for (const mobile of [false, true]) {
  test(`同一名单接收企微和邮件，抄送仅邮件且可限制接收人（${mobile ? "手机" : "桌面"}）`, async ({
    page,
  }) => {
    if (mobile) await page.setViewportSize({ width: 390, height: 844 });
    const controls = await fixture(page);
    const dialog = await openPreview(page);
    await expect(
      dialog.getByRole("button", { name: "采用建议名单（3 人）" }),
    ).toBeVisible();
    await expect(
      selection(dialog, "通知接收人").locator(".el-tag"),
    ).toHaveCount(3);
    await expect(
      dialog.getByText("所选人员同时接收企业微信纪要通知和邮件。", {
        exact: false,
      }),
    ).toBeVisible();
    await removePerson(dialog, "第二责任人");
    await selection(dialog, "通知接收人").locator(".el-select").click();
    await expect(
      page.getByRole("option", { name: "无关部门人员", exact: true }),
    ).toHaveCount(0);
    await dialog.getByRole("heading", { name: title, exact: true }).click();
    await selection(dialog, "邮件抄送人或邮箱").locator(".el-select").click();
    await dialog
      .getByRole("combobox", { name: "邮件抄送人或邮箱", exact: true })
      .fill("仅邮件");
    await expect(
      page.getByRole("option", { name: "隔离主持人", exact: true }),
    ).toHaveCount(0);
    await page
      .getByRole("option", {
        name: "仅邮件同事 · cc@example.test",
        exact: true,
      })
      .click();
    await dialog.getByRole("heading", { name: title, exact: true }).click();
    await expect(
      page.getByRole("option", {
        name: "仅邮件同事 · cc@example.test",
        exact: true,
      }),
    ).toBeHidden();
    await dialog.screenshot({
      path: path.join(
        output,
        `shared-notifications-${mobile ? "mobile" : "desktop"}.png`,
      ),
    });
    await dialog.getByRole("button", { name: "确认发布", exact: true }).click();
    await expect.poll(() => controls.publications.length).toBe(1);
    const sent = controls.publications[0];
    expect(sent.command.notify).toBe(true);
    expect(sent.command.shared_recipients).toBe(true);
    expect(sent.draft.recipient_ids).toEqual(["host", "owner"]);
    expect(sent.draft.cc_ids).toEqual(["host", "cc"]);
    expect(sent.draft.cc_emails).toEqual([]);
    expect(controls.unexpected).toEqual([]);
    expect(controls.errors).toEqual([]);
  });
}

test("清空接收人后重新打开预览保持为空，并在发布前提示", async ({ page }) => {
  const controls = await fixture(page);
  let dialog = await openPreview(page);
  for (const name of ["隔离主持人", "隔离责任人", "第二责任人"])
    await removePerson(dialog, name);
  await expect(
    dialog.getByText("请选择至少一位通知接收人，或取消发送通知后仅发布。", {
      exact: true,
    }),
  ).toBeVisible();
  await dialog.getByRole("button", { name: "返回编辑", exact: true }).click();
  dialog = await openPreview(page);
  await expect(selection(dialog, "通知接收人").locator(".el-tag")).toHaveCount(
    0,
  );
  await dialog.getByRole("button", { name: "确认发布", exact: true }).click();
  await expect(
    page
      .getByText("请选择至少一位通知接收人，或取消发送通知后仅发布。", {
        exact: true,
      })
      .last(),
  ).toBeVisible();
  expect(controls.publications).toEqual([]);
  expect(controls.errors).toEqual([]);
});

for (const mobile of [false, true]) {
  test(`抄送支持搜索人员邮箱、手填及批量粘贴，保存后保留（${mobile ? "手机" : "桌面"}）`, async ({
    page,
  }) => {
    if (mobile) await page.setViewportSize({ width: 390, height: 844 });
    const controls = await fixture(page);
    let dialog = await openPreview(page);
    let input = dialog.getByRole("combobox", {
      name: "邮件抄送人或邮箱",
      exact: true,
    });
    await input.fill("cc@example");
    await page
      .getByRole("option", {
        name: "仅邮件同事 · cc@example.test",
        exact: true,
      })
      .click();
    await input.fill("external@example.com");
    await page
      .getByRole("option", { name: "external@example.com", exact: true })
      .click();
    await input.evaluate((element) => {
      const data = new DataTransfer();
      data.setData(
        "text/plain",
        "finance@example.com；external@EXAMPLE.com\nreview@example.com",
      );
      element.dispatchEvent(
        new ClipboardEvent("paste", {
          bubbles: true,
          cancelable: true,
          clipboardData: data,
        }),
      );
    });
    await input.fill("bad-address");
    await input.press("Enter");
    await expect(
      page.getByText("请输入完整邮箱地址：bad-address", { exact: true }),
    ).toBeVisible();
    await dialog.getByRole("button", { name: "返回编辑", exact: true }).click();
    await page.getByRole("button", { name: "保存草稿", exact: true }).click();
    await expect
      .poll(() => controls.saved.at(-1)?.cc_emails)
      .toEqual([
        "external@example.com",
        "finance@example.com",
        "review@example.com",
      ]);
    await page.reload();
    dialog = await openPreview(page);
    input = dialog.getByRole("combobox", {
      name: "邮件抄送人或邮箱",
      exact: true,
    });
    await expect(
      selection(dialog, "邮件抄送人或邮箱").getByText(
        "仅邮件同事 · cc@example.test",
        { exact: true },
      ),
    ).toBeVisible();
    await selection(dialog, "邮件抄送人或邮箱").locator(".el-select").click();
    await dialog.getByRole("heading", { name: title, exact: true }).click();
    await dialog.screenshot({
      path: path.join(output, `cc-emails-${mobile ? "mobile" : "desktop"}.png`),
    });
    await dialog.getByRole("button", { name: "确认发布", exact: true }).click();
    await expect.poll(() => controls.publications.length).toBe(1);
    expect(controls.publications[0].draft.cc_ids).toEqual(["host", "cc"]);
    expect(controls.publications[0].draft.cc_emails).toEqual([
      "external@example.com",
      "finance@example.com",
      "review@example.com",
    ]);
    expect(controls.errors).toEqual([]);
    expect(controls.unexpected).toEqual([]);
  });
}

test("打开人员选择时刷新超过一分钟的通讯录，新入职可选、离职隐藏", async ({
  page,
}) => {
  const controls = await fixture(page);
  await page.clock.install();
  const dialog = await openPreview(page);
  controls.users.find((user) => user.id === "cc").active = false;
  controls.users.push({
    ...controls.users[1],
    id: "new-hire",
    name: "金丽君",
    email: "jin@example.com",
  });
  await page.clock.fastForward(61000);
  const input = dialog.getByRole("combobox", {
    name: "邮件抄送人或邮箱",
    exact: true,
  });
  const rosterReads = controls.rosterReads;
  await selection(dialog, "邮件抄送人或邮箱").locator(".el-select").click();
  await expect.poll(() => controls.rosterReads).toBe(rosterReads + 1);
  await input.fill("金丽君");
  await expect(
    page.getByRole("option", { name: "金丽君 · jin@example.com", exact: true }),
  ).toBeVisible();
  await input.fill("仅邮件同事");
  await expect(
    page.getByRole("option", {
      name: "仅邮件同事 · cc@example.test",
      exact: true,
    }),
  ).toHaveCount(0);
  expect(controls.errors).toEqual([]);
});

for (const mobile of [false, true]) {
  test(`抄送可按姓名添加未参会的跨部门员工并保存发布（${mobile ? "手机" : "桌面"}）`, async ({
    page,
  }) => {
    if (mobile) await page.setViewportSize({ width: 390, height: 844 });
    const controls = await fixture(page);
    controls.companyUsers.push({
      ...controls.users.find((user) => user.id === "outsider"),
      id: "new-company-hire",
      name: "金丽君",
      email: "jin@example.test",
      wecom_id: null,
    });
    let dialog = await openPreview(page);
    let input = dialog.getByRole("combobox", {
      name: "邮件抄送人或邮箱",
      exact: true,
    });
    const ccField = selection(dialog, "邮件抄送人或邮箱").locator(".el-select");
    await ccField.click();
    await expect(page.getByRole("option")).toHaveCount(0);
    await input.press("ArrowDown");
    await expect(page.getByRole("option")).toHaveCount(0);
    await input.fill("金");
    await expect(
      page.getByRole("option", {
        name: "金丽君 · jin@example.test",
        exact: true,
      }),
    ).toBeVisible();
    await expect(
      page.getByRole("option", { name: "金丽", exact: true }),
    ).toHaveCount(0);
    await expect(
      page.getByRole("option", {
        name: "无关部门人员 · other@example.test",
        exact: true,
      }),
    ).toHaveCount(0);
    await input.fill("");
    await expect(page.getByRole("option")).toHaveCount(0);
    await ccField.click();
    await expect(page.getByRole("option")).toHaveCount(0);
    await input.fill("金丽");
    await expect(
      page.getByRole("option", {
        name: "金丽君 · jin@example.test",
        exact: true,
      }),
    ).toBeVisible();
    await expect(
      page.locator(".el-select__popper:visible").last(),
    ).not.toHaveClass(/enter-active|enter-from/);
    await page.screenshot({ path: test.info().outputPath("cc-search.png") });
    await input.press("Enter");
    await expect(page.getByRole("option")).toHaveCount(0);
    await ccField.click();
    await expect(page.getByRole("option")).toHaveCount(0);
    await expect(
      selection(dialog, "邮件抄送人或邮箱").getByText(
        "金丽君 · jin@example.test",
        { exact: true },
      ),
    ).toBeVisible();
    await input.fill("无关部门人员");
    await page
      .getByRole("option", {
        name: "无关部门人员 · other@example.test",
        exact: true,
      })
      .click();
    await dialog.getByRole("button", { name: "返回编辑", exact: true }).click();
    await page.getByRole("button", { name: "保存草稿", exact: true }).click();
    await expect
      .poll(() => controls.saved.at(-1)?.cc_ids)
      .toEqual(["host", "new-company-hire", "outsider"]);
    await page.reload();
    dialog = await openPreview(page);
    await expect(
      selection(dialog, "邮件抄送人或邮箱").getByText(
        "金丽君 · jin@example.test",
        { exact: true },
      ),
    ).toBeVisible();
    await dialog.getByRole("button", { name: "确认发布", exact: true }).click();
    await expect.poll(() => controls.publications.length).toBe(1);
    const sent = controls.publications[0].draft;
    expect(sent.cc_ids).toEqual(["host", "new-company-hire", "outsider"]);
    expect(sent.cc_emails).toEqual([]);
    expect(sent.recipient_ids).toEqual(["host", "owner", "second"]);
    expect(sent.attendee_ids).toEqual(["host"]);
    expect(sent.items[0].owner_ids).toEqual(["owner", "second"]);
    expect(controls.errors).toEqual([]);
    expect(controls.unexpected).toEqual([]);
  });
}

for (const width of [1440, 390]) {
  test(`参会人员和主持人自动抄送，保留手填且重复打开不重复（${width}px）`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 960 });
    const controls = await fixture(
      page,
      ["owner"],
      ["host", "cc", "incomplete", "cc"],
    );
    let dialog = await openPreview(page);
    await expect
      .poll(() => controls.saved.at(-1)?.cc_ids)
      .toEqual(["host", "cc"]);
    await expect(
      dialog.getByText("以下人员尚未配置邮箱，未自动加入抄送：未配置管理员。"),
    ).toBeVisible();
    const input = dialog.getByRole("combobox", {
      name: "邮件抄送人或邮箱",
      exact: true,
    });
    await input.fill("extra@example.com");
    await page
      .getByRole("option", { name: "extra@example.com", exact: true })
      .click();
    await dialog.getByRole("button", { name: "返回编辑", exact: true }).click();
    dialog = await openPreview(page);
    await expect
      .poll(() => controls.saved.at(-1)?.cc_ids)
      .toEqual(["host", "cc"]);
    await expect
      .poll(() => controls.saved.at(-1)?.cc_emails)
      .toEqual(["extra@example.com"]);
    await expect(selection(dialog, "邮件抄送人或邮箱")).toContainText(
      "隔离主持人 · host@example.test",
    );
    await expect(selection(dialog, "邮件抄送人或邮箱")).toContainText(
      "仅邮件同事 · cc@example.test",
    );
    await dialog.screenshot({
      path: path.resolve(
        `../../.validation/cross-department-tasks-20261103/automatic-cc-${width}.png`,
      ),
    });
    await dialog.getByRole("button", { name: "确认发布", exact: true }).click();
    await expect.poll(() => controls.publications.length).toBe(1);
    expect(controls.publications[0].draft.cc_ids).toEqual(["host", "cc"]);
    expect(controls.publications[0].draft.recipient_ids).toEqual(["owner"]);
    expect(controls.errors).toEqual([]);
    expect(controls.unexpected).toEqual([]);
  });
}

test("缺失邮箱和企微绑定时显示人员姓名，关闭通知可仅发布", async ({ page }) => {
  const controls = await fixture(page, ["incomplete"]);
  const dialog = await openPreview(page);
  await expect(
    dialog.getByText("未配置管理员尚未配置邮箱和企业微信绑定。", {
      exact: true,
    }),
  ).toBeVisible();
  await dialog.getByRole("button", { name: "确认发布", exact: true }).click();
  expect(controls.publications).toEqual([]);
  await dialog.getByText("不通知", { exact: true }).click();
  await expect(
    dialog.getByText("未配置管理员尚未配置邮箱和企业微信绑定。", {
      exact: true,
    }),
  ).toHaveCount(0);
  await dialog.getByRole("button", { name: "确认发布", exact: true }).click();
  await expect.poll(() => controls.publications.length).toBe(1);
  expect(controls.publications[0].command).toMatchObject({
    notify: false,
    shared_recipients: true,
  });
  expect(controls.publications[0].draft.recipient_ids).toEqual([
    "host",
    "incomplete",
  ]);
  expect(controls.unexpected).toEqual([]);
  expect(controls.errors).toEqual([]);
});

for (const width of [1366, 390]) {
  test(`历史发布后提示下期修订草稿补齐数量 ${width}`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    const controls = await fixture(page);
    controls.supplement = {
      added: 40,
      skipped: 0,
      target_date: "2026-11-02",
      revision: true,
    };
    const dialog = await openPreview(page);
    await dialog.getByRole("button", { name: "确认发布", exact: true }).click();
    await expect(
      page.getByText(
        "已补齐 2026-11-02 会议的 40 项上期任务，请到修订草稿核对后发布",
        { exact: true },
      ),
    ).toBeVisible();
    await expect.poll(() => controls.publications.length).toBe(1);
    expect(controls.errors).toEqual([]);
    expect(controls.unexpected).toEqual([]);
    await page.screenshot({
      path: path.resolve(
        `../../.validation/history-auto-supplement-20261113/publication-message-${width}.png`,
      ),
      fullPage: true,
    });
  });
}
