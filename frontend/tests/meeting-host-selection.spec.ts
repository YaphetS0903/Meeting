import { expect, test, type Page } from "@playwright/test";
import { fileURLToPath } from "node:url";

async function fixture(page: Page, existing = false) {
  const me = {
    id: "creator",
    name: "普通编制员工",
    active: true,
    is_admin: false,
    department_id: "it",
    can_assign_tasks: false,
    can_oversee_tasks: false,
  };
  const local = { ...me, id: "local", name: "本部门同事" };
  const external = {
    ...me,
    id: "external",
    name: "张主持",
    department_id: "finance",
    department_name: "财务部",
  };
  const disabled = {
    ...external,
    id: "disabled",
    name: "张离职",
    active: false,
  };
  const control = {
    saved: [] as any[],
    errors: [] as string[],
    unexpected: [] as string[],
    rosterCalls: 0,
    version: 1,
    draft: null as any,
  };
  const record = () => ({
    id: "saved-host-meeting",
    ...control.draft,
    draft: control.draft,
    draft_version: control.version,
    published_version: 0,
    has_draft: true,
    can_edit: true,
    versions: [],
    attachments: [],
    pending_edit_requests: [],
  });
  if (existing)
    control.draft = {
      title: "既有会议草稿",
      kind: "department",
      department_id: "it",
      series_id: "weekly",
      host_id: me.id,
      recorder_id: me.id,
      reviewer_id: null,
      meeting_date: "2026-11-02",
      meeting_time: "",
      location: "",
      attendee_ids: [],
      absentees: "",
      decisions: "",
      items: [],
      attachment_ids: [],
      recipient_ids: [],
      cc_ids: [],
      cc_emails: [],
    };
  await page.addInitScript(() =>
    sessionStorage.setItem("token", "isolated-companywide-host"),
  );
  page.on("pageerror", (error) => control.errors.push(error.message));
  await page.route("**/api/**", async (route) => {
    const request = route.request(),
      url = new URL(request.url());
    let body: any = [];
    if (url.pathname === "/api/me") body = me;
    else if (url.pathname === "/api/auth/options") body = { wecom: false };
    else if (url.pathname === "/api/directory")
      body = {
        users: [me, local, disabled],
        departments: [{ id: "it", name: "信息部" }],
        series: [{ id: "weekly", name: "信息部周会" }],
      };
    else if (url.pathname === "/api/meeting-people") {
      control.rosterCalls++;
      body = { users: [me, local, external] };
    } else if (
      url.pathname === "/api/meetings" &&
      request.method() === "POST"
    ) {
      control.draft = request.postDataJSON();
      control.saved.push(structuredClone(control.draft));
      body = record();
    } else if (url.pathname === "/api/meetings/saved-host-meeting") {
      if (request.method() === "PUT") {
        control.draft = request.postDataJSON().draft;
        control.version++;
        control.saved.push(structuredClone(control.draft));
      }
      body = record();
    } else if (url.pathname.startsWith("/api/native-todo/"))
      body = { enabled: false };
    else control.unexpected.push(`${request.method()} ${url.pathname}`);
    await route.fulfill({ json: body });
  });
  await page.goto(
    existing
      ? "/meetings/saved-host-meeting"
      : "/meetings/new?series=series:weekly",
  );
  if (existing)
    await page.getByRole("button", { name: "编辑", exact: true }).click();
  await expect(
    page.getByRole("combobox", { name: "主持人", exact: true }),
  ).toBeVisible();
  await expect.poll(() => control.rosterCalls).toBeGreaterThan(0);
  return control;
}

for (const width of [1440, 390]) {
  test(`普通员工可搜索全公司在职主持人，保存后回显姓名和参会身份 ${width}px`, async ({
    page,
  }, testInfo) => {
    await page.setViewportSize({ width, height: 960 });
    const control = await fixture(page);
    await page.getByPlaceholder("请输入会议名称").fill("跨部门主持会议");
    const host = page.getByRole("combobox", { name: "主持人", exact: true });
    await host.fill("张");
    await expect(
      page.getByRole("option", { name: "张离职", exact: true }),
    ).toHaveCount(0);
    await page.getByRole("option", { name: "张主持", exact: true }).click();
    await page
      .getByRole("button", { name: "确认并填写任务单", exact: true })
      .click();
    await expect(
      page.locator(".attendee-person").filter({ hasText: "张主持" }),
    ).toContainText("主持人");
    const attendeeCard = page.locator(".attendee-card");
    await expect(attendeeCard.locator(".attendee-person")).toHaveCount(1);
    await expect(attendeeCard.locator(".count")).toHaveText("1 人");
    await expect(
      attendeeCard
        .locator(".attendee-person")
        .filter({ hasText: "普通编制员工" }),
    ).toHaveCount(0);

    // Recording and attendance are independent: the recorder can attend,
    // then leave the attendee list without changing the recording identity.
    for (const attending of [true, false]) {
      await page.getByRole("button", { name: "＋ 新增", exact: true }).click();
      const participants = page.getByRole("dialog", {
        name: "新增或调整参会人员",
        exact: true,
      });
      await expect(participants).toContainText("现场参会人员");
      await expect(participants).toContainText(
        `参会人员（共 ${attending ? 1 : 2} 人）`,
      );
      await expect(
        participants.locator(".participant-dialog-host"),
      ).toContainText("张主持 · 主持人");
      await participants
        .getByRole("combobox", { name: "现场参会人员", exact: true })
        .fill("普通编制员工");
      await page
        .getByRole("option", { name: "普通编制员工", exact: true })
        .click();
      await participants
        .getByRole("button", { name: "确认", exact: true })
        .click();
      await expect(attendeeCard.locator(".attendee-person")).toHaveCount(
        attending ? 2 : 1,
      );
      await page.getByRole("button", { name: "保存草稿", exact: true }).click();
      await expect
        .poll(() => control.saved.at(-1)?.attendee_ids)
        .toEqual(attending ? ["creator"] : []);
      expect(control.saved.at(-1).recorder_id).toBe("creator");
      await page.reload();
      await page.getByRole("button", { name: "编辑", exact: true }).click();
      await expect(attendeeCard.locator(".count")).toHaveText(
        attending ? "2 人" : "1 人",
      );
    }
    await page.getByRole("button", { name: "保存草稿", exact: true }).click();
    await expect.poll(() => control.saved.at(-1)?.host_id).toBe("external");
    expect(control.saved.at(-1).recorder_id).toBe("creator");
    expect(control.saved.at(-1).attendee_ids).toEqual([]);
    await expect(page).toHaveURL(/\/meetings\/saved-host-meeting/);
    await page.reload();
    await page.getByRole("button", { name: "编辑", exact: true }).click();
    await expect(
      page.locator(".el-form-item").filter({ has: host }),
    ).toContainText("张主持");
    await expect(
      page.locator(".attendee-person").filter({ hasText: "张主持" }),
    ).toContainText("主持人");
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBeTruthy();
    await page.screenshot({
      path: testInfo.outputPath(`host-${width}.png`),
      fullPage: true,
      animations: "disabled",
    });
    expect(control.errors).toEqual([]);
    expect(control.unexpected).toEqual([]);
  });
}

test("本人主持的既有草稿也可改为跨部门在职主持人", async ({ page }) => {
  const control = await fixture(page, true);
  const host = page.getByRole("combobox", { name: "主持人", exact: true });
  await host.fill("张主");
  await page.getByRole("option", { name: "张主持", exact: true }).click();
  await page.getByRole("button", { name: "保存草稿", exact: true }).click();
  await expect.poll(() => control.saved.at(-1)?.host_id).toBe("external");
  expect(control.errors).toEqual([]);
  expect(control.unexpected).toEqual([]);
});
