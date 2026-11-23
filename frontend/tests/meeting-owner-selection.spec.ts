import { expect, test, type Page, type Locator } from "@playwright/test";
import path from "node:path";
import { fileURLToPath } from "node:url";
const evidence = fileURLToPath(
  new URL(
    "../../../.validation/historical-owner-publish-20261102",
    import.meta.url,
  ),
);

async function expandMobileRows(page: Page) {
  if ((page.viewportSize()?.width || 1440) >= 768) return;
  await expect(
    page.locator(".meeting-plan-table tbody tr").first(),
  ).toBeVisible();
  const collapsed = page.getByRole("button", { name: /^展开第\d+项任务$/ });
  while (await collapsed.count()) await collapsed.first().click();
}

async function selectNameWithMouse(page: Page, label: Locator) {
  await label.scrollIntoViewIfNeeded();
  await label.click({ trial: true });
  await page.evaluate(() => document.fonts.ready);
  const rect = await label.boundingBox();
  if (!rect) throw new Error("姓名不可见");
  await page.mouse.move(rect.x + 1, rect.y + rect.height / 2);
  await page.mouse.down();
  await page.mouse.move(rect.x + rect.width + 8, rect.y + rect.height / 2, {
    steps: 10,
  });
  await page.evaluate(
    () => new Promise((resolve) => requestAnimationFrame(resolve)),
  );
  await page.mouse.up();
}

async function fixture(page: Page, admin = false, selectedCount = 0) {
  const host = {
    id: "host",
    name: "会议主持人",
    active: true,
    is_admin: admin,
    department_id: "finance",
  };
  const users = [
    host,
    { id: "a", name: "张三", active: true, department_id: "it" },
    { id: "b", name: "李四", active: true, department_id: "it" },
    {
      id: "c",
      name: "王五",
      active: true,
      department_id: "finance",
      department_ids: ["finance", "it"],
    },
    { id: "outside", name: "财务员工", active: true, department_id: "finance" },
    { id: "other-zhang", name: "张三", active: true, department_id: "finance" },
    { id: "same-a", name: "陈同名", active: true, department_id: "it" },
    { id: "same-b", name: "陈同名", active: true, department_id: "it" },
    { id: "inactive", name: "停用员工", active: false, department_id: "it" },
    ...Array.from({ length: 85 }, (_, i) => ({
      id: `staff-${i}`,
      name: `信息员工${i}`,
      active: true,
      department_id: "it",
    })),
  ];
  const control = {
    rosterRequests: 0,
    draft: {
      title: "信息部会议",
      kind: "department",
      department_id: "it",
      series_id: null,
      host_id: host.id,
      recorder_id: host.id,
      reviewer_id: null,
      meeting_date: "2026-10-27",
      meeting_time: "14:00",
      location: "信息部",
      attendee_ids: Array.from(
        { length: selectedCount },
        (_, i) => `staff-${i}`,
      ),
      absentees: "",
      decisions: "",
      recipient_ids: [],
      cc_ids: [],
      attachment_ids: [],
      items: [
        {
          id: "item",
          section: "new",
          content: "测试多人责任人",
          requirements: "",
          category: "",
          owner_ids: [] as string[],
          status: "未到期",
          due_date: "2026-11-01",
          verifier_id: host.id,
          deliverable: "",
          notes: "",
          exclusion_reason: "",
        },
      ],
    },
    version: 1,
    saved: [] as any[],
    errors: [] as string[],
    unexpected: [] as string[],
  };
  const hire = {
    id: "hire",
    name: "金丽君",
    active: true,
    department_id: "finance",
    department_ids: ["finance"],
    department_name: "总经办",
    email: "jin@example.com",
    wecom_id: "wecom-jin",
  };
  const prefix = { ...hire, id: "hire-prefix", name: "金丽君助理" };
  const contains = { ...hire, id: "hire-contains", name: "原金丽君工作对接人" };
  const record = () => ({
    id: "owner-draft",
    draft: control.draft,
    draft_version: control.version,
    has_draft: true,
    published_version: 0,
    can_edit: true,
    versions: [],
    attachments: [],
    pending_edit_requests: [],
  });
  await page.addInitScript(() =>
    sessionStorage.setItem("token", "isolated-owners-test"),
  );
  page.on("pageerror", (e) => control.errors.push(e.message));
  await page.route("**/api/**", async (route) => {
    const request = route.request(),
      url = new URL(request.url());
    let response: any;
    if (url.pathname === "/api/me") response = host;
    else if (url.pathname === "/api/auth/options") response = { wecom: false };
    else if (url.pathname === "/api/directory")
      response = {
        users: control.draft.attendee_ids.includes("hire")
          ? [...users, hire]
          : users,
        departments: [
          { id: "it", name: "信息部" },
          { id: "finance", name: "财务部" },
        ],
        series: [],
      };
    else if (url.pathname === "/api/meeting-people") {
      control.rosterRequests++;
      response = {
        users: [
          ...users.filter(
            (user) =>
              user.active ||
              url.searchParams.get("include_inactive") === "true",
          ),
          contains,
          prefix,
          hire,
        ],
      };
    } else if (url.pathname === "/api/meetings/owner-draft") {
      if (request.method() === "PUT") {
        control.draft = request.postDataJSON().draft;
        control.version++;
        control.saved.push(structuredClone(control.draft));
      }
      response = record();
    } else if (url.pathname.startsWith("/api/native-todo/"))
      response = {
        enabled: false,
        meetings_enabled: false,
        meeting_dispatch_available: false,
      };
    else {
      control.unexpected.push(url.pathname);
      response = [];
    }
    await route.fulfill({ json: response });
  });
  await page.goto("/meetings/owner-draft");
  await expect(
    page.getByRole("combobox", { name: "责任人", exact: true }),
  ).toBeVisible();
  return control;
}

for (const mobile of [false, true]) {
  test(`搜索精确姓名排在已有参会人员和模糊匹配之前，滚动回顶部（${mobile ? "手机" : "桌面"}）`, async ({
    page,
  }, testInfo) => {
    if (mobile) await page.setViewportSize({ width: 390, height: 844 });
    const control = await fixture(page, false, 7);
    await expect(page.locator(".attendee-card .count")).toHaveText("8 人");
    await page.getByRole("button", { name: "＋ 新增", exact: true }).click();
    const dialog = page.getByRole("dialog", {
      name: "新增或调整参会人员",
      exact: true,
    });
    await expect(dialog).toContainText("参会人员（共 8 人）");
    await expect(dialog.locator(".participant-dialog-host")).toContainText(
      "会议主持人 · 主持人",
    );
    const selected = dialog.locator(".people-sortable-tag");
    await expect(selected).toHaveText(
      Array.from({ length: 7 }, (_, i) => `信息员工${i}`),
    );
    for (const tag of await selected.all()) await expect(tag).toBeVisible();
    await expect(dialog).not.toContainText("+4");
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBeTruthy();
    await dialog.screenshot({
      path: testInfo.outputPath("attendee-roster.png"),
      animations: "disabled",
    });
    const input = dialog.getByRole("combobox", {
      name: "现场参会人员",
      exact: true,
    });
    await input.focus();
    await input.press("ArrowDown");
    const list = page.locator(
      ".meeting-participants-popper:visible .el-select-dropdown__wrap",
    );
    await list.evaluate((element) => {
      element.scrollTop = element.scrollHeight;
    });
    await expect
      .poll(() => list.evaluate((element) => element.scrollTop))
      .toBeGreaterThan(0);
    await input.fill("金丽君");
    await expect(page.getByRole("option").first()).toHaveText("金丽君");
    await expect(page.getByRole("option").nth(1)).toHaveText("金丽君助理");
    await expect(page.getByRole("option").nth(2)).toHaveText(
      "原金丽君工作对接人",
    );
    await expect
      .poll(() => list.evaluate((element) => element.scrollTop))
      .toBe(0);
    await expect(page.getByRole("option").first()).toBeInViewport();
    await input.press("Enter");
    await dialog.getByRole("button", { name: "确认", exact: true }).click();
    await page.getByRole("button", { name: "保存草稿", exact: true }).click();
    await expect
      .poll(() => control.saved.at(-1)?.attendee_ids.at(-1))
      .toBe("hire");
    expect(control.saved.at(-1).attendee_ids).toHaveLength(8);
    await page.getByRole("button", { name: "＋ 新增", exact: true }).click();
    await expect(dialog).toContainText("参会人员（共 9 人）");
    await expect(selected).toHaveText([
      ...Array.from({ length: 7 }, (_, i) => `信息员工${i}`),
      "金丽君",
    ]);
    // Selecting the already counted host must not add another attendee.
    await input.fill("会议主持人");
    await page.getByRole("option", { name: "会议主持人", exact: true }).click();
    await expect(dialog).toContainText("参会人员（共 9 人）");
    await expect(dialog.locator(".participant-dialog-host")).toHaveCount(1);
    expect(control.errors).toEqual([]);
  });
}

for (const admin of [false, true]) {
  for (const mobile of [false, true]) {
    test(`${admin ? "管理员" : "主持人"}可直接选择全公司在职责任人，无需先添加参会人员（${mobile ? "手机" : "桌面"}）`, async ({
      page,
    }) => {
      if (mobile) await page.setViewportSize({ width: 390, height: 844 });
      const control = await fixture(page, admin);
      const owner = page.getByRole("combobox", { name: "责任人", exact: true });
      await owner.fill("金丽君");
      await page.getByRole("option", { name: "金丽君", exact: true }).click();
      await owner.fill("财务员工");
      await page.getByRole("option", { name: "财务员工", exact: true }).click();
      await page.getByRole("button", { name: "保存草稿", exact: true }).click();
      await expect
        .poll(() => control.saved.at(-1)?.items[0].owner_ids)
        .toEqual(["hire", "outside"]);
      expect(control.saved.at(-1).attendee_ids).toEqual([]);
      expect(control.rosterRequests).toBeGreaterThanOrEqual(1);
      await page.reload();
      await expandMobileRows(page);
      await expect(
        page.locator(
          ".people-select-copyable:has(input[aria-label=责任人]) .el-tag",
        ),
      ).toHaveText(["金丽君", "财务员工"]);
      expect(control.errors).toEqual([]);
      expect(control.unexpected).toEqual([]);
    });
  }
}

async function paste(input: Locator, text: string) {
  await input.focus();
  await input.press("ArrowDown");
  await input.evaluate((element, value) => {
    const clipboardData = new DataTransfer();
    clipboardData.setData("text/plain", value);
    element.dispatchEvent(
      new ClipboardEvent("paste", {
        bubbles: true,
        cancelable: true,
        clipboardData,
      }),
    );
  }, text);
}

for (const mobile of [false, true]) {
  test(`补录离职责任人后交接给在职人员，原姓名及备注可保存和追溯（${mobile ? "手机" : "桌面"}）`, async ({
    page,
  }) => {
    if (mobile) await page.setViewportSize({ width: 390, height: 960 });
    const control = await fixture(page);
    const owner = page.getByRole("combobox", { name: "责任人", exact: true });
    const row = page.getByRole("row").filter({ has: owner });
    const notes = row.getByRole("textbox", { name: "备注", exact: true });
    await notes.fill("原有备注");
    await owner.fill("停用员工");
    await page
      .getByRole("option", { name: "停用员工（已停用）", exact: true })
      .click();
    await expect(row.getByRole("status")).toContainText(
      "按历史任务补录，可保留原责任人发布",
    );
    await page.getByRole("button", { name: "保存草稿", exact: true }).click();
    await expect
      .poll(() => control.saved.at(-1)?.items[0].owner_ids)
      .toEqual(["inactive"]);
    expect(control.saved.at(-1)?.items[0].historical_owner_record).toBe(true);
    await page.reload();
    await expandMobileRows(page);
    const field = page.locator(".people-select-field").filter({ has: owner });
    await expect(field.locator(".el-tag")).toHaveText(["停用员工（已停用）"]);
    await row
      .locator("td")
      .nth(2)
      .screenshot({
        path: path.join(
          evidence,
          `historical-owner-hint-${mobile ? "mobile" : "desktop"}.png`,
        ),
      });
    await field.screenshot({
      path: path.join(
        evidence,
        `inactive-owner-${mobile ? "mobile" : "desktop"}.png`,
      ),
    });
    // Check the close button through its native keyboard activation after the
    // screenshot has repositioned the scroll container.
    await field.locator(".el-tag__close").focus();
    await page.keyboard.press("Space");
    await expect(field.locator(".el-tag")).toHaveCount(0);
    const history = "原有备注\n原责任人：停用员工（已停用）";
    await expect(notes).toHaveValue(history);
    await owner.fill("李四");
    await page.getByRole("option", { name: "李四", exact: true }).click();
    await expect(notes).toHaveValue(history);
    await expect(row.getByRole("status")).toHaveCount(0);
    await owner.fill("停用员工");
    await page
      .getByRole("option", { name: "停用员工（已停用）", exact: true })
      .click();
    await field
      .locator(".el-tag")
      .filter({ hasText: "停用员工" })
      .locator(".el-tag__close")
      .click();
    await expect(notes).toHaveValue(history);
    await page.getByRole("button", { name: "保存草稿", exact: true }).click();
    await expect
      .poll(() => control.saved.at(-1)?.items[0].owner_ids)
      .toEqual(["b"]);
    expect(control.saved.at(-1)?.items[0].notes).toBe(history);
    expect(control.saved.at(-1)?.items[0].historical_owner_record).toBe(false);
    await page.reload();
    await expandMobileRows(page);
    await expect(field.locator(".el-tag")).toHaveText(["李四"]);
    await expect(notes).toHaveValue(history);
    await page.evaluate(() => {
      if (document.activeElement instanceof HTMLElement)
        document.activeElement.blur();
      window.scrollTo(0, 0);
    });
    await page.screenshot({
      path: path.join(
        evidence,
        `handover-${mobile ? "mobile" : "desktop"}.png`,
      ),
      fullPage: true,
      animations: "disabled",
    });
    await notes.screenshot({
      path: path.join(
        evidence,
        `handover-notes-${mobile ? "mobile" : "desktop"}.png`,
      ),
    });
    expect(control.errors).toEqual([]);
    expect(control.unexpected).toEqual([]);
  });
}

for (const linked of [false, true]) {
  test(`既有离职责任人草稿${linked ? "延续安排仍需换人" : "无需重新选人即可按历史记录保存"}`, async ({
    page,
  }) => {
    const control = await fixture(page);
    control.draft.items[0].owner_ids = ["inactive"];
    if (linked) (control.draft.items[0] as any).task_id = "previous-task";
    await page.reload();
    await expandMobileRows(page);
    const owner = page.getByRole("combobox", { name: "责任人", exact: true });
    const row = page.getByRole("row").filter({ has: owner });
    await expect(row.getByRole("status")).toContainText(
      linked
        ? "发布延续安排前请更换责任人"
        : "按历史任务补录，可保留原责任人发布",
    );
    if (linked)
      await row
        .getByRole("textbox", { name: "备注", exact: true })
        .fill("延续备注");
    await page.getByRole("button", { name: "保存草稿", exact: true }).click();
    await expect.poll(() => control.saved.length).toBeGreaterThan(0);
    expect(
      control.saved.at(-1)?.items[0].historical_owner_record === true,
    ).toBe(!linked);
    expect(control.errors).toEqual([]);
    expect(control.unexpected).toEqual([]);
  });
}

test("补录离职姓名不允许将停用员工选为主持人或验证人", async ({ page }) => {
  await fixture(page);
  await page.getByRole("button", { name: "编辑", exact: true }).click();
  const host = page.getByRole("combobox", { name: "主持人", exact: true });
  await host.fill("停用员工");
  await expect(page.getByRole("option", { name: /停用员工/ })).toHaveCount(0);
  await host.press("Escape");
  const verifier = page.getByRole("combobox", { name: "验证人", exact: true });
  await verifier.fill("停用员工");
  await expect(page.getByRole("option", { name: /停用员工/ })).toHaveCount(0);
});

test("从右侧空白搜索区向左拖选姓名可复制，松手不重新聚焦输入框", async ({
  page,
  context,
}) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  const control = await fixture(page);
  const input = page.getByRole("combobox", { name: "责任人", exact: true });
  await paste(input, "王五");
  const field = page.locator(".people-select-field").filter({ has: input });
  const label = field.locator(".people-select-name");
  await expect(label).toHaveText("王五");
  await input.focus();
  await label.click({ trial: true });
  const searchBox = await input.boundingBox();
  const nameBox = await label.boundingBox();
  if (!searchBox || !nameBox) throw new Error("姓名与搜索区必须可见");
  await page.mouse.move(
    searchBox.x + searchBox.width / 2,
    searchBox.y + searchBox.height / 2,
  );
  await page.mouse.down();
  await page.mouse.move(nameBox.x + 1, nameBox.y + nameBox.height / 2, {
    steps: 12,
  });
  await page.mouse.up();
  await expect
    .poll(() => page.evaluate(() => window.getSelection()?.toString()))
    .toBe("王五");
  await page.keyboard.press("Control+c");
  await expect
    .poll(() => page.evaluate(() => navigator.clipboard.readText()))
    .toBe("王五");
  await expect(page.locator(".el-select__popper:visible")).toHaveCount(0);
  await field.screenshot({ path: path.join(evidence, "right-drag-owner.png") });

  // Clicking the same blank region must still allow typing another name.
  await page.mouse.click(
    searchBox.x + searchBox.width / 2,
    searchBox.y + searchBox.height / 2,
  );
  await expect(input).toBeFocused();
  await page.keyboard.type("李四");
  await page.getByRole("option", { name: "李四", exact: true }).click();
  await input.fill("会议主持人");
  await page.getByRole("option", { name: "会议主持人", exact: true }).click();
  await expect(field.locator(".people-select-name")).toHaveText([
    "王五",
    "李四",
    "会议主持人",
  ]);
  await input.focus();
  await input.click({ trial: true });
  const nextSearchBox = await input.boundingBox();
  const firstNameBox = await field
    .locator(".people-select-name")
    .first()
    .boundingBox();
  if (!nextSearchBox || !firstNameBox)
    throw new Error("多人姓名与搜索区必须可见");
  await page.mouse.move(
    nextSearchBox.x + nextSearchBox.width / 2,
    nextSearchBox.y + nextSearchBox.height / 2,
  );
  await page.mouse.down();
  await page.mouse.move(
    firstNameBox.x + 1,
    firstNameBox.y + firstNameBox.height / 2,
    { steps: 12 },
  );
  await page.mouse.up();
  await page.keyboard.press("Control+c");
  const copied = await page.evaluate(() => navigator.clipboard.readText());
  expect(copied).toBe("王五、李四、会议主持人");
  await field.screenshot({
    path: path.join(evidence, "right-drag-multiple.png"),
  });
  await page.getByRole("button", { name: "＋ 新增工作", exact: true }).click();
  await paste(
    page.getByRole("combobox", { name: "责任人", exact: true }).nth(1),
    copied,
  );
  await page.getByRole("button", { name: "保存草稿", exact: true }).click();
  expect(control.saved.at(-1)?.items[0].owner_ids).toEqual(["c", "b", "host"]);
  expect(control.saved.at(-1)?.items[1].owner_ids).toEqual(["c", "b", "host"]);
  expect(control.errors).toEqual([]);
});

for (const mobile of [false, true]) {
  test(`验证人全员可选，输入后才展开，选中姓名可复制到下一条并保存重开（${mobile ? "手机" : "桌面"}）`, async ({
    page,
    context,
  }) => {
    if (mobile) await page.setViewportSize({ width: 390, height: 844 });
    await context.grantPermissions(["clipboard-read", "clipboard-write"]);
    const control = await fixture(page);
    const input = page
      .getByRole("combobox", { name: "验证人", exact: true })
      .first();
    const field = page
      .locator(".people-select-field")
      .filter({
        has: page.getByRole("combobox", { name: "验证人", exact: true }),
      })
      .first();
    const label = field.locator(".people-select-name");
    await expect(label).toHaveText("会议主持人");
    await field.locator(".el-select__wrapper").click();
    await expect(page.locator(".el-select__popper:visible")).toHaveCount(0);
    await input.press("ArrowDown");
    await expect(input).toHaveAttribute("aria-expanded", "false");
    await input.fill("金");
    await expect(
      page.getByRole("option", { name: "金丽君", exact: true }),
    ).toBeVisible();
    await page.getByRole("option", { name: "金丽君", exact: true }).click();
    await expect(label).toHaveText("金丽君");
    await expect(page.locator(".el-select__popper:visible")).toHaveCount(0);
    await input.focus();
    await input.press("Control+c");
    await expect
      .poll(() => page.evaluate(() => navigator.clipboard.readText()))
      .toBe("金丽君");
    await expect(page.getByText("复制姓名", { exact: true })).toHaveCount(0);
    const copied = await page.evaluate(() => navigator.clipboard.readText());
    expect(copied).toBe("金丽君");
    if (!mobile) {
      await selectNameWithMouse(page, label);
      await expect
        .poll(() => page.evaluate(() => window.getSelection()?.toString()))
        .toBe("金丽君");
      await page.keyboard.press("Control+c");
      await expect
        .poll(() => page.evaluate(() => navigator.clipboard.readText()))
        .toBe("金丽君");
      // Verifiers share the same blank-to-name drag interaction as owners.
      await input.click({ trial: true });
      const searchBox = await input.boundingBox();
      const nameBox = await label.boundingBox();
      if (!searchBox || !nameBox) throw new Error("验证人姓名与搜索区不可见");
      const startX = searchBox.x + searchBox.width - 2;
      await page.mouse.move(startX, searchBox.y + searchBox.height / 2);
      await page.mouse.down();
      await page.mouse.move(nameBox.x + 1, nameBox.y + nameBox.height / 2, {
        steps: 12,
      });
      await page.mouse.up();
      await expect
        .poll(() => page.evaluate(() => window.getSelection()?.toString()))
        .toBe("金丽君");
      await page.keyboard.press("Control+c");
      await expect
        .poll(() => page.evaluate(() => navigator.clipboard.readText()))
        .toBe("金丽君");
    }
    await field.screenshot({
      path: path.join(
        evidence,
        `verifier-${mobile ? "mobile" : "desktop"}.png`,
      ),
    });
    await page
      .getByRole("button", { name: "＋ 新增工作", exact: true })
      .click();
    const second = page
      .getByRole("combobox", { name: "验证人", exact: true })
      .nth(1);
    await paste(second, copied);
    await paste(second, "李四、财务员工");
    await expect(
      page.getByText("验证人每项只能选择一人，请分别选择", { exact: true }),
    ).toBeVisible();
    await input.fill("张三");
    await page
      .getByRole("option", { name: "张三 · 财务部", exact: true })
      .click();
    await expect(label).toHaveText("张三 · 财务部");
    await page.getByRole("button", { name: "保存草稿", exact: true }).click();
    await expect
      .poll(() => control.saved.at(-1)?.items[0].verifier_id)
      .toBe("other-zhang");
    expect(control.saved.at(-1)?.items[1].verifier_id).toBe("hire");
    expect(control.saved.at(-1)?.attendee_ids).toEqual([]);
    await page.reload();
    await expandMobileRows(page);
    await expect(label).toHaveText("张三 · 财务部");
    await expect(
      page
        .locator(".people-select-field")
        .filter({
          has: page.getByRole("combobox", { name: "验证人", exact: true }),
        })
        .nth(1)
        .locator(".people-select-name"),
    ).toHaveText("金丽君");
    await input.fill("停用员工");
    await expect(
      page.getByRole("option", { name: "停用员工", exact: true }),
    ).toHaveCount(0);
    await input.fill("");
    await expect(page.locator(".el-select__popper:visible")).toHaveCount(0);
    expect(control.errors).toEqual([]);
    expect(control.unexpected).toEqual([]);
  });
}

test("已选姓名可原生选中文字复制，也可在空输入框 Ctrl+C 复制全部", async ({
  page,
  context,
}) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  const control = await fixture(page);
  const input = page.getByRole("combobox", { name: "责任人", exact: true });
  await paste(input, "张三 · 信息部、李四、金丽君、财务员工");
  const field = page.locator(".people-select-field").filter({ has: input });
  await expect(
    field.getByRole("button", { name: "复制责任人", exact: true }),
  ).toHaveCount(0);
  await input.focus();
  await input.press("Control+c");
  await expect
    .poll(() => page.evaluate(() => navigator.clipboard.readText()))
    .toBe("张三 · 信息部、李四、金丽君、财务员工");

  // A real mouse selection must survive the select wrapper's click handler.
  const label = field
    .locator(".people-select-name")
    .filter({ hasText: "金丽君" });
  await selectNameWithMouse(page, label);
  await expect
    .poll(() => page.evaluate(() => window.getSelection()?.toString()))
    .toBe("金丽君");
  await page.keyboard.press("Control+c");
  await expect
    .poll(() => page.evaluate(() => navigator.clipboard.readText()))
    .toBe("金丽君");

  await input.fill("李四");
  await input.selectText();
  await input.press("Control+c");
  await expect
    .poll(() => page.evaluate(() => navigator.clipboard.readText()))
    .toBe("李四");
  await input.press("Escape");
  await page.getByRole("button", { name: "保存草稿", exact: true }).click();
  expect(control.saved.at(-1)?.items[0].owner_ids).toEqual([
    "a",
    "b",
    "hire",
    "outside",
  ]);
  expect(control.errors).toEqual([]);
});

for (const admin of [false, true])
  test(`${admin ? "管理员" : "主持人"}责任人可选全公司在职员工，也可补录已停用原责任人`, async ({
    page,
  }) => {
    const control = await fixture(page, admin);
    const input = page.getByRole("combobox", { name: "责任人", exact: true });
    for (const name of ["会议主持人", "王五", "张三", "财务员工"]) {
      await input.fill(name);
      await expect(
        page.getByRole("option", {
          name: name === "张三" ? "张三 · 信息部" : name,
          exact: true,
        }),
      ).toBeVisible();
    }
    for (const name of ["停用员工"]) {
      await input.fill(name);
      await expect(
        page.getByRole("option", { name: `${name}（已停用）`, exact: true }),
      ).toBeVisible();
    }
    expect(control.errors).toEqual([]);
  });

test("一次粘贴三人能保存，支持表格换行与多种分隔，重复粘贴去重", async ({
  page,
}) => {
  const control = await fixture(page);
  const input = page.getByRole("combobox", { name: "责任人", exact: true });
  await paste(input, "张三 · 信息部、李四，王五");
  await expect(page.getByText("已添加 3 人", { exact: true })).toBeVisible();
  await paste(input, "张三 · 信息部\t李四\r\n王五；张三 · 信息部");
  await expect(
    page.getByText("这些人员已在已选名单中", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "保存草稿", exact: true }).click();
  await expect
    .poll(() => control.saved.at(-1)?.items[0].owner_ids)
    .toEqual(["a", "b", "c"]);
  expect(control.errors).toEqual([]);
  expect(control.unexpected).toEqual([]);
});

test("重名及未识别姓名提示核对，外部门匹配部分仍能保存", async ({ page }) => {
  const control = await fixture(page);
  await paste(
    page.getByRole("combobox", { name: "责任人", exact: true }),
    "张三 陈同名 财务员工 未知人员",
  );
  await expect(
    page.getByText(/存在重名，请分别选择：张三、陈同名/),
  ).toBeVisible();
  await expect(page.getByText(/未匹配或不在可选范围：未知人员/)).toBeVisible();
  await page.getByRole("button", { name: "保存草稿", exact: true }).click();
  await expect
    .poll(() => control.saved.at(-1)?.items[0].owner_ids)
    .toEqual(["outside"]);
});

test("粘贴可找到未列在前80项的成员，单人键盘搜索仍可选", async ({ page }) => {
  const control = await fixture(page);
  const input = page.getByRole("combobox", { name: "责任人", exact: true });
  await paste(input, "信息员工84/王五");
  await input.fill("李四");
  await page.getByRole("option", { name: "李四", exact: true }).click();
  await page.getByRole("button", { name: "保存草稿", exact: true }).click();
  await expect
    .poll(() => control.saved.at(-1)?.items[0].owner_ids)
    .toEqual(["staff-84", "c", "b"]);
});

test("切换会议部门后仍可选择全公司人员，既有责任人保留", async ({ page }) => {
  await fixture(page, true);
  const input = page.getByRole("combobox", { name: "责任人", exact: true });
  await paste(input, "张三 · 信息部、李四");
  await page.getByRole("button", { name: "编辑", exact: true }).click();
  await page
    .getByRole("combobox", { name: "会议所属部门", exact: true })
    .click();
  await page.getByRole("option", { name: "财务部", exact: true }).click();
  await input.fill("财务员工");
  await expect(
    page.getByRole("option", { name: "财务员工", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("option").filter({ hasText: "信息员工" }),
  ).toHaveCount(0);
  await expect(
    page.locator(".el-tag").filter({ hasText: "李四" }).first(),
  ).toBeVisible();
  await page.screenshot({
    path: path.join(evidence, "desktop.png"),
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({
    path: path.join(evidence, "mobile.png"),
    fullPage: true,
  });
});

for (const mobile of [false, true]) {
  test(`责任人输入后才展开，点击即时保留姓名，支持连续选择和删除（${mobile ? "手机" : "桌面"}）`, async ({
    page,
  }) => {
    if (mobile) await page.setViewportSize({ width: 390, height: 844 });
    const control = await fixture(page);
    const input = page.getByRole("combobox", { name: "责任人", exact: true });
    const select = page.locator(
      ".meeting-plan-table .people-select-copyable:has(input[aria-label=责任人])",
    );
    const popup = page.locator(".el-select__popper:visible");
    const idle = async () => {
      await expect(popup).toHaveCount(0);
      await expect(input).toHaveAttribute("aria-expanded", "false");
    };
    await select.locator(".el-select__wrapper").click();
    await idle();
    await page.screenshot({
      path: path.join(evidence, `idle-${mobile ? "mobile" : "desktop"}.png`),
    });
    await input.press("ArrowDown");
    await idle();
    await input.fill(" ");
    await idle();
    await input.evaluate((element) => {
      const field = element as HTMLInputElement;
      field.dispatchEvent(
        new CompositionEvent("compositionstart", { bubbles: true }),
      );
      field.value = "zhang";
      field.dispatchEvent(
        new InputEvent("input", {
          bubbles: true,
          data: "zhang",
          isComposing: true,
        }),
      );
      const commit = new KeyboardEvent("keydown", {
        bubbles: true,
        cancelable: true,
        key: "Enter",
        isComposing: true,
      });
      if (!field.dispatchEvent(commit))
        throw new Error("姓名输入法确认不应被阻止");
    });
    await expect(popup).toHaveCount(0);
    await input.evaluate((element) => {
      const field = element as HTMLInputElement;
      field.value = "张";
      field.dispatchEvent(
        new CompositionEvent("compositionend", { bubbles: true, data: "张" }),
      );
      field.dispatchEvent(
        new InputEvent("input", { bubbles: true, data: "张" }),
      );
    });
    await expect(
      page.getByRole("option", { name: "张三 · 信息部", exact: true }),
    ).toBeVisible();
    await page.screenshot({
      path: path.join(evidence, `typed-${mobile ? "mobile" : "desktop"}.png`),
    });
    await input.press("Escape");
    await idle();
    await input.fill("张");
    await input.fill("");
    await idle();
    await select.locator(".el-select__wrapper").click();
    await idle();
    await input.fill("张三");
    await page
      .getByRole("heading", { name: "本次会议任务部署", exact: true })
      .click();
    await select.locator(".el-select__wrapper").click();
    await idle();
    await input.fill("张三");
    await page
      .getByRole("option", { name: "张三 · 信息部", exact: true })
      .click();
    await expect(select.locator(".el-tag")).toHaveText(["张三 · 信息部"]);
    await expect(input).toBeFocused();
    await idle();
    await input.fill("李四");
    await expect(
      page.getByRole("option", { name: "李四", exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole("option", { name: "张三 · 信息部", exact: true }),
    ).toHaveCount(0);
    await input.press("ArrowDown");
    await input.press("Enter");
    await expect(select.locator(".el-tag")).toHaveText([
      "张三 · 信息部",
      "李四",
    ]);
    await expect(input).toBeFocused();
    await select.locator(".el-tag").first().locator(".el-tag__close").click();
    await expect(select.locator(".el-tag")).toHaveText(["李四"]);
    await page.getByRole("button", { name: "保存草稿", exact: true }).click();
    await expect
      .poll(() => control.saved.at(-1)?.items[0].owner_ids)
      .toEqual(["b"]);
    await page.reload();
    await expandMobileRows(page);
    await expect(select.locator(".el-tag")).toHaveText(["李四"]);
    expect(control.errors).toEqual([]);
    await select.screenshot({
      path: path.join(evidence, `owner-${mobile ? "mobile" : "desktop"}.png`),
    });
  });

  test(`复制责任人并粘贴到下一条任务，支持单人和多人（${mobile ? "手机" : "桌面"}）`, async ({
    page,
    context,
  }) => {
    if (mobile) await page.setViewportSize({ width: 390, height: 844 });
    await context.grantPermissions(["clipboard-read", "clipboard-write"]);
    const control = await fixture(page);
    const first = page
      .getByRole("combobox", { name: "责任人", exact: true })
      .first();
    await first.fill("张三");
    await page
      .getByRole("option", { name: "张三 · 信息部", exact: true })
      .click();
    await first.focus();
    await first.press("Control+c");
    await expect(page.getByText("复制姓名", { exact: true })).toHaveCount(0);
    const one = await page.evaluate(() => navigator.clipboard.readText());
    expect(one).toBe("张三 · 信息部");
    await page
      .getByRole("button", { name: "＋ 新增工作", exact: true })
      .click();
    const second = page
      .getByRole("combobox", { name: "责任人", exact: true })
      .nth(1);
    await paste(second, one);
    await expect(
      page
        .locator(".people-select-copyable:has(input[aria-label=责任人])")
        .nth(1)
        .locator(".el-tag"),
    ).toHaveText(["张三 · 信息部"]);
    await first.fill("李四");
    await page.getByRole("option", { name: "李四", exact: true }).click();
    await first.focus();
    await first.press("Control+c");
    const several = await page.evaluate(() => navigator.clipboard.readText());
    expect(several).toBe("张三 · 信息部、李四");
    await paste(second, several);
    await paste(second, several);
    await page.getByRole("button", { name: "保存草稿", exact: true }).click();
    await expect
      .poll(() => control.saved.at(-1)?.items[0].owner_ids)
      .toEqual(["a", "b"]);
    expect(control.saved.at(-1)?.items[1].owner_ids).toEqual(["a", "b"]);
    await page.reload();
    await expandMobileRows(page);
    await expect(
      page
        .locator(".people-select-copyable:has(input[aria-label=责任人])")
        .nth(1)
        .locator(".el-tag"),
    ).toHaveText(["张三 · 信息部", "李四"]);
    await page
      .locator(".people-select-field")
      .filter({
        has: page
          .getByRole("combobox", { name: "责任人", exact: true })
          .first(),
      })
      .first()
      .screenshot({
        path: path.join(evidence, `copy-${mobile ? "mobile" : "desktop"}.png`),
      });
    expect(control.errors).toEqual([]);
    expect(control.unexpected).toEqual([]);
  });
}
