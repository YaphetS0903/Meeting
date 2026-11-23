import { expect, test, type Page } from "@playwright/test";
import { fileURLToPath } from "node:url";

const evidence = fileURLToPath(
  new URL(
    "../../../.validation/meeting-add-work-affix-20261105/",
    import.meta.url,
  ),
);

async function setup(page: Page, published = false) {
  const host = {
    id: "host",
    name: "测试主持人",
    active: true,
    is_admin: false,
    department_id: "dep",
  };
  const items = Array.from({ length: 30 }, (_, index) => ({
    id: `task-${index + 1}`,
    section: "new",
    category: "",
    content: `第${index + 1}项工作内容`,
    requirements: "",
    owner_ids: [host.id],
    due_date: "2026-11-20",
    status: "未到期",
    verifier_id: null,
    deliverable: "工作报告",
    notes: "备注",
    exclusion_reason: "",
  }));
  let draft: any = {
    title: "多行任务编辑验证",
    series_id: "s",
    department_id: "dep",
    kind: "department",
    host_id: host.id,
    recorder_id: host.id,
    meeting_date: "2026-11-05",
    meeting_time: "09:00",
    location: "会议室",
    reviewer_id: null,
    attendee_ids: [],
    attachment_ids: [],
    recipient_ids: [],
    cc_ids: [],
    cc_emails: [],
    absentees: "",
    decisions: "",
    items,
  };
  let version = 1;
  const writes: any[] = [],
    errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.addInitScript(() =>
    sessionStorage.setItem("token", "isolated-editor-actions"),
  );
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    let body: any;
    if (path === "/api/me") body = host;
    else if (path === "/api/auth/options") body = { wecom: false };
    else if (path === "/api/directory")
      body = {
        users: [host],
        departments: [{ id: "dep", name: "测试部门" }],
        series: [{ id: "s", name: "测试系列", department_id: "dep" }],
      };
    else if (path === "/api/meeting-people")
      body = { users: [host], departments: [{ id: "dep", name: "测试部门" }] };
    else if (path === "/api/meetings/actions") {
      if (route.request().method() === "PUT") {
        expect(published).toBe(false);
        draft = route.request().postDataJSON().draft;
        writes.push(structuredClone(draft));
        version++;
      } else expect(route.request().method()).toBe("GET");
      body = {
        id: "actions",
        ...draft,
        draft,
        draft_version: version,
        has_draft: !published,
        published_version: published ? 1 : 0,
        can_edit: true,
        can_delete: true,
        versions: published ? [{ number: 1, snapshot: draft }] : [],
        attachments: [],
        context: {},
      };
    } else
      throw new Error(
        `Unexpected API call: ${route.request().method()} ${path}`,
      );
    await route.fulfill({ json: body });
  });
  return { writes, errors, draft: () => draft };
}

for (const width of [2105, 1366, 390]) {
  test(`新增工作保留原位，滚动后固定在发布下方，拖动图标位于序号左侧 ${width}`, async ({
    page,
  }) => {
    const height = width === 2105 ? 1262 : 900;
    await page.setViewportSize({ width, height });
    const result = await setup(page);
    await page.goto("/meetings/actions");
    const actions = page.getByRole("region", { name: "会议编辑操作" });
    const add = page.getByRole("button", { name: "＋ 新增工作", exact: true });
    const rows = page.locator(".meeting-plan-table tbody > tr");
    const heading = page.locator("header.section-card-heading").filter({
      has: page.getByRole("heading", { name: "本次会议任务部署", exact: true }),
    });
    await expect(rows).toHaveCount(30);
    await expect(add).toHaveCount(1);
    await expect(
      actions.getByRole("button", { name: "＋ 新增工作", exact: true }),
    ).toHaveCount(0);
    const originalTop = await heading.evaluate(
      (node) => window.scrollY + node.getBoundingClientRect().top,
    );
    const normalScroll = await actions.evaluate(
      (bar, top) =>
        top -
        bar.getBoundingClientRect().height -
        document.querySelector(".topbar")!.getBoundingClientRect().height -
        100,
      originalTop,
    );
    await page.evaluate(
      (y) => window.scrollTo({ top: y, behavior: "instant" }),
      Math.max(0, normalScroll),
    );
    await expect(
      heading.getByRole("button", { name: "＋ 新增工作", exact: true }),
    ).toBeVisible();
    const normal = (await add.boundingBox())!,
      header = (await heading.boundingBox())!;
    expect(normal.y).toBeGreaterThanOrEqual(header.y);
    expect(normal.y + normal.height).toBeLessThanOrEqual(
      header.y + header.height,
    );
    await page.screenshot({
      path: evidence + `original-position-${width}.png`,
      fullPage: false,
      animations: "disabled",
    });
    for (const position of [
      originalTop + 100,
      Number.MAX_SAFE_INTEGER,
      originalTop + 600,
    ]) {
      await page.evaluate(
        (y) => window.scrollTo({ top: y, behavior: "instant" }),
        position,
      );
      await expect
        .poll(() =>
          add.evaluate((node) => {
            const box = node.getBoundingClientRect();
            return (
              box.top >= 0 &&
              box.bottom <= window.innerHeight &&
              box.left >= 0 &&
              box.right <= window.innerWidth &&
              node.contains(
                document.elementFromPoint(
                  box.x + box.width / 2,
                  box.y + box.height / 2,
                ),
              )
            );
          }),
        )
        .toBe(true);
      const box = (await add.boundingBox())!;
      const preview = (await actions
        .getByRole("button", { name: "预览并发布", exact: true })
        .boundingBox())!;
      const toolbar = (await actions.boundingBox())!;
      expect(box.y).toBeGreaterThan(preview.y + preview.height);
      const sectionHeader = (await heading.boundingBox())!;
      expect(sectionHeader.y).toBeGreaterThanOrEqual(
        toolbar.y + toolbar.height - 1,
      );
      expect(box.y).toBeGreaterThanOrEqual(sectionHeader.y);
      expect(box.y + box.height).toBeLessThanOrEqual(
        sectionHeader.y + sectionHeader.height,
      );
      expect(
        Math.abs(box.x + box.width - preview.x - preview.width),
      ).toBeLessThan(30);
    }
    await page.screenshot({
      path: evidence + `fixed-below-publish-${width}.png`,
      fullPage: false,
      animations: "disabled",
    });
    await page.evaluate(
      (y) => window.scrollTo({ top: y, behavior: "instant" }),
      Math.max(0, normalScroll),
    );
    await expect(
      heading.getByRole("button", { name: "＋ 新增工作", exact: true }),
    ).toBeVisible();
    await rows.nth(9).scrollIntoViewIfNeeded();
    const handle = rows.nth(9).locator(".task-drag-handle");
    const number = rows.nth(9).locator(".task-order-index > span");
    const h = (await handle.boundingBox())!,
      n = (await number.boundingBox())!;
    expect(h.x + h.width).toBeLessThan(n.x);
    expect(Math.abs(h.y + h.height / 2 - n.y - n.height / 2)).toBeLessThan(2);
    await add.click();
    await expect(rows).toHaveCount(31);
    await actions
      .getByRole("button", { name: "保存草稿", exact: true })
      .click();
    await expect.poll(() => result.writes.length).toBeGreaterThan(0);
    expect(
      result
        .draft()
        .items.slice(0, 30)
        .map((item: any) => item.id),
    ).toEqual(Array.from({ length: 30 }, (_, i) => `task-${i + 1}`));
    expect(result.draft().items[30]).toMatchObject({
      section: "new",
      content: "",
      owner_ids: [],
      due_date: null,
    });
    expect(result.errors).toEqual([]);
  });
}

test("已发布只读页面不出现新增按钮，保留任务搜索", async ({ page }) => {
  const result = await setup(page, true);
  await page.goto("/meetings/actions");
  await expect(page.locator(".meeting-plan-table tbody > tr")).toHaveCount(30);
  await expect(
    page.getByRole("button", { name: "＋ 新增工作", exact: true }),
  ).toHaveCount(0);
  await page
    .getByRole("textbox", { name: "搜索本次工作", exact: true })
    .fill("第10项");
  await expect(page.locator(".meeting-plan-table tbody > tr")).toHaveCount(1);
  expect(result.writes).toEqual([]);
  expect(result.errors).toEqual([]);
});
