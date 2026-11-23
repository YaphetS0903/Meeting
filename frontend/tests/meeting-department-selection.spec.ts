import { expect, test, type Page } from "@playwright/test";
import { fileURLToPath } from "node:url";

const evidence = fileURLToPath(
  new URL(
    "../../../.validation/meeting-department-selector-20261104/",
    import.meta.url,
  ),
);
const departments = [
  { id: "company", name: "天瑞公司", parent_id: null },
  { id: "tech", name: "技术中心", parent_id: "company" },
  { id: "commerce", name: "商务中心", parent_id: "company" },
  { id: "project", name: "项目部", parent_id: "commerce" },
];

async function fixture(page: Page, existing = true) {
  const me = {
    id: "recorder",
    name: "跨部门记录员",
    active: true,
    is_admin: false,
    department_id: "tech",
  };
  const host = {
    ...me,
    id: "host",
    name: "项目部主持人",
    department_id: "project",
  };
  const control = {
    saved: [] as any[],
    errors: [] as string[],
    unexpected: [] as string[],
    version: 1,
    draft: {
      title: "项目部周例会",
      department_id: "tech",
      series_id: "weekly",
      kind: "department",
      meeting_date: "2026-11-04",
      meeting_time: "10:00",
      location: "会议室",
      host_id: host.id,
      recorder_id: me.id,
      reviewer_id: null,
      attendee_ids: [],
      absentees: "",
      decisions: "",
      items: [],
      attachment_ids: [],
      recipient_ids: [],
      cc_ids: [],
      cc_emails: [],
    } as any,
  };
  const record = () => ({
    id: "department-meeting",
    ...control.draft,
    draft: control.draft,
    draft_version: control.version,
    published_version: 0,
    has_draft: true,
    can_edit: true,
    can_delete: false,
    versions: [],
    attachments: [],
    pending_edit_requests: [],
    notifications: [],
  });
  await page.addInitScript(() =>
    sessionStorage.setItem("token", "isolated-meeting-department"),
  );
  page.on("pageerror", (e) => control.errors.push(e.message));
  await page.route("**/api/**", async (route) => {
    const request = route.request(),
      path = new URL(request.url()).pathname;
    let body: any = [];
    if (path === "/api/me") body = me;
    else if (path === "/api/auth/options") body = { wecom: false };
    else if (path === "/api/directory")
      body = {
        users: [me],
        departments: departments.filter((d) =>
          ["company", "tech"].includes(d.id),
        ),
        series: [
          { id: "weekly", name: "项目部周会", department_id: "project" },
        ],
      };
    else if (path === "/api/meeting-people")
      body = { users: [me, host], departments };
    else if (path === "/api/meetings" && request.method() === "POST") {
      control.draft = request.postDataJSON();
      control.saved.push(structuredClone(control.draft));
      body = record();
    } else if (path === "/api/meetings/department-meeting") {
      if (request.method() === "PUT") {
        control.draft = request.postDataJSON().draft;
        control.version++;
        control.saved.push(structuredClone(control.draft));
      }
      body = record();
    } else if (path.startsWith("/api/native-todo/")) body = { enabled: false };
    else control.unexpected.push(`${request.method()} ${path}`);
    await route.fulfill({ json: body });
  });
  await page.goto(
    existing
      ? "/meetings/department-meeting"
      : "/meetings/new?series=series:weekly",
  );
  await page.getByRole("button", { name: "编辑", exact: true }).click();
  await expect(
    page.getByRole("combobox", { name: "会议所属部门", exact: true }),
  ).toBeVisible();
  return control;
}

for (const width of [1366, 390]) {
  test(`cross-department recorder can search, save and reload the actual meeting department at ${width}px`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 960 });
    const control = await fixture(page);
    const department = page.getByRole("combobox", {
      name: "会议所属部门",
      exact: true,
    });
    await department.fill("项目部");
    const option = page.getByRole("option", {
      name: "天瑞公司 / 商务中心 / 项目部",
      exact: true,
    });
    await expect(option).toBeVisible();
    await page.screenshot({
      path: `${evidence}/departments-${width}.png`,
      fullPage: true,
    });
    await option.click();
    await page.getByRole("button", { name: "保存草稿", exact: true }).click();
    await expect
      .poll(() => control.saved.at(-1)?.department_id)
      .toBe("project");
    expect(control.saved.at(-1).recorder_id).toBe("recorder");
    await page.reload();
    await page.getByRole("button", { name: "编辑", exact: true }).click();
    await expect(
      page.locator(".el-form-item").filter({ has: department }),
    ).toContainText("天瑞公司 / 商务中心 / 项目部");
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBeTruthy();
    expect(control.errors).toEqual([]);
    expect(control.unexpected).toEqual([]);
  });
}

test("new meeting defaults to its visible series department instead of the recorder's organization", async ({
  page,
}) => {
  const control = await fixture(page, false);
  const department = page.getByRole("combobox", {
    name: "会议所属部门",
    exact: true,
  });
  await expect(
    page.locator(".el-form-item").filter({ has: department }),
  ).toContainText("天瑞公司 / 商务中心 / 项目部");
  const title = page
    .locator(".el-form-item")
    .filter({ hasText: "会议名称" })
    .getByRole("textbox");
  await title.fill("新建项目部周例会");
  await page.getByRole("button", { name: "保存草稿", exact: true }).click();
  await expect.poll(() => control.saved.at(-1)?.department_id).toBe("project");
  expect(control.errors).toEqual([]);
  expect(control.unexpected).toEqual([]);
});
