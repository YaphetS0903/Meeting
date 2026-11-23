import { expect, test, type Page } from "@playwright/test";
import fs from "node:fs";
import { fileURLToPath } from "node:url";

const evidence = fileURLToPath(
  new URL("../../../.validation/review-task-drag-20261123/", import.meta.url),
);
const users = Array.from({ length: 13 }, (_, i) => ({
  id: i === 0 ? "host" : `u${i}`,
  name: i === 0 ? "主持甲" : `员工${i}`,
  active: true,
  department_id: "dept",
  email: `person${i}@example.test`,
  wecom_id: `person${i}`,
}));
const row = (id: string, section: string, status = "未到期") => ({
  id,
  section,
  status,
  category: "类别",
  content: `工作${id}`,
  requirements: "",
  owner_ids: ["u1"],
  verifier_id: "host",
  due_date: "2026-11-20",
  deliverable: `输出${id}`,
  notes: `备注${id}`,
  exclusion_reason: "",
});

async function setup(
  page: Page,
  readOnly = false,
  withExtensions = false,
  overrides: { draft?: Record<string, any>; missingEmail?: string } = {},
) {
  const roster = [...users].reverse().map((user) => ({
    ...user,
    email: user.id === overrides.missingEmail ? "" : user.email,
  }));
  let draft: any = {
    title: "任务顺序与抄送人验收",
    department_id: "dept",
    series_id: null,
    kind: "department",
    host_id: "host",
    recorder_id: "host",
    meeting_date: "2026-11-03",
    attendee_ids: users.slice(0, 12).map((u) => u.id),
    attachment_ids: [],
    recipient_ids: [],
    cc_ids: [],
    cc_emails: [],
    items: [
      row("review-a", "review", "已完成"),
      row("a", "new"),
      row("review-b", "review", "未完成"),
      row("b", "new"),
      row("c", "new"),
      { ...row("review-future", "review"), task_id: "linked", task_version: 2 },
      {
        ...row("carried", "new"),
        content: "工作review-future",
        deliverable: "输出review-future",
        notes: "备注review-future",
        task_id: "linked",
        task_version: 2,
      },
    ],
  };
  if (withExtensions) {
    draft.items.push(
      {
        ...row("extension-a", "new"),
        task_id: "extended-task-a",
        task_version: 1,
        source: { extended_from_item_id: "review-b" },
      },
      {
        ...row("extension-b", "new"),
        task_id: "extended-task-b",
        task_version: 1,
        source: { extended_from_item_id: "review-b" },
      },
    );
  }
  Object.assign(draft, overrides.draft || {});
  const initial = structuredClone(draft),
    writes: any[] = [],
    previews: any[] = [],
    errors: string[] = [];
  let version = 1;
  page.on("pageerror", (e) => errors.push(e.message));
  await page.addInitScript(() =>
    sessionStorage.setItem("token", "isolated-task-order"),
  );
  await page.route("**/api/**", async (route) => {
    const request = route.request(),
      path = new URL(request.url()).pathname;
    let body: any;
    if (path === "/api/me") body = users[0];
    else if (path === "/api/auth/options") body = { wecom: false };
    else if (path === "/api/directory")
      body = {
        users: roster,
        departments: [{ id: "dept", name: "测试部门" }],
        series: [],
      };
    else if (path === "/api/meeting-people") body = { users: roster };
    else if (path === "/api/meetings/check-order/preview") {
      previews.push(structuredClone(draft));
      return route.fulfill({
        path: fileURLToPath(
          new URL("./fixtures/preview-minutes.pdf", import.meta.url),
        ),
        contentType: "application/pdf",
      });
    } else if (path === "/api/meetings/check-order") {
      if (request.method() === "PUT") {
        expect(readOnly).toBe(false);
        expect(request.postDataJSON().expected_version).toBe(version);
        draft = request.postDataJSON().draft;
        writes.push(structuredClone(draft));
        version++;
      } else expect(request.method()).toBe("GET");
      body = {
        id: "check-order",
        draft,
        draft_version: version,
        published_version: readOnly ? 1 : 0,
        can_edit: true,
        has_draft: !readOnly,
        historical: false,
        attachments: [],
        versions: readOnly ? [{ number: 1, snapshot: draft }] : [],
        assessments: {},
      };
    } else
      throw new Error(`Unexpected API request: ${request.method()} ${path}`);
    await route.fulfill({ json: body });
  });
  return { initial, writes, previews, errors, draft: () => draft };
}

for (const width of [1366, 390]) {
  test(`extensions have separate headings and stable group order at ${width}px`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 900 });
    const result = await setup(page, false, true);
    await page.goto("/meetings/check-order");
    const table = page.locator(".meeting-plan-table");
    const rows = table.locator("tr[data-arrangement-id]");
    const ids = () =>
      rows.evaluateAll((nodes) =>
        nodes.map((node) => (node as HTMLElement).dataset.arrangementId),
      );
    await expect(table.locator(".arrangement-group-heading")).toHaveText([
      "本次新建工作",
      "上次未完成延续事项",
    ]);
    await expect(
      page.getByRole("textbox", { name: "关联任务变更原因", exact: true }),
    ).toHaveCount(0);
    await expect
      .poll(ids)
      .toEqual(["a", "b", "c", "carried", "extension-a", "extension-b"]);
    const handle = (id: string) =>
      table.locator(`[data-arrangement-id="${id}"] .task-drag-handle`);
    if (width >= 768) {
      await table.evaluate((node) =>
        window.scrollTo({
          top: window.scrollY + node.getBoundingClientRect().top - 260,
          behavior: "instant",
        }),
      );
      await handle("extension-b").dragTo(
        table.locator('[data-arrangement-id="extension-a"] td').first(),
        { targetPosition: { x: 4, y: 4 } },
      );
    } else {
      await handle("extension-b").press("ArrowUp");
    }
    await expect
      .poll(ids)
      .toEqual(["a", "b", "c", "carried", "extension-b", "extension-a"]);
    await handle("extension-a").press("ArrowDown");
    await expect
      .poll(ids)
      .toEqual(["a", "b", "c", "carried", "extension-b", "extension-a"]);
    await handle("a").press("ArrowUp");
    await expect
      .poll(ids)
      .toEqual(["a", "b", "c", "carried", "extension-b", "extension-a"]);
    await page.getByRole("button", { name: "保存草稿", exact: true }).click();
    expect(
      result
        .draft()
        .items.filter((row: any) => row.section === "new")
        .map((row: any) => row.id),
    ).toEqual(["a", "b", "c", "carried", "extension-b", "extension-a"]);
    await page.reload();
    await expect
      .poll(ids)
      .toEqual(["a", "b", "c", "carried", "extension-b", "extension-a"]);
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth),
    ).toBeLessThanOrEqual(width);
    await expect(page.locator(".el-loading-mask")).toHaveCount(0);
    await table.screenshot({
      path: fileURLToPath(
        new URL(
          `../../../.validation/review-task-drag-20261123/groups-${width}.png`,
          import.meta.url,
        ),
      ),
    });
    expect(result.errors).toEqual([]);
  });
}

for (const width of [1366, 390]) {
  test(`dragging task order saves, reopens and reaches the preview at ${width}px`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 900 });
    const result = await setup(page);
    await page.goto("/meetings/check-order");
    const table = page.locator(".meeting-plan-table"),
      rows = table.locator("tbody tr");
    const contents = () =>
      rows
        .getByRole("textbox", { name: "工作内容及要求", exact: true })
        .evaluateAll((nodes: HTMLTextAreaElement[]) =>
          nodes.map((n) => n.value.replace(/^类别\n/, "")),
        );
    await expect(rows).toHaveCount(4);
    await expect(table.locator(".task-drag-handle")).toHaveCount(4);
    await expect(
      table.getByRole("button", { name: /^上移|^下移/ }),
    ).toHaveCount(0);
    await expect(
      page.locator(".meeting-review-table .task-drag-handle"),
    ).toHaveCount(3);
    const move = async (id: string, target: string) => {
      // Keep both rows below the sticky header and action bar before starting
      // the mouse gesture; dragging an off-screen row would scroll mid-gesture.
      await table.evaluate((node) => {
        const header = document
          .querySelector(".topbar")!
          .getBoundingClientRect();
        const actions = document
          .querySelector(".meeting-save-actions")!
          .getBoundingClientRect();
        const heading = document
          .querySelector(".meeting-deployment-heading")!
          .getBoundingClientRect();
        const tableHead = node.querySelector("thead")!.getBoundingClientRect();
        window.scrollTo({
          top:
            window.scrollY +
            node.getBoundingClientRect().top -
            header.height -
            actions.height -
            heading.height -
            tableHead.height -
            16,
          behavior: "instant",
        });
      });
      await expect
        .poll(() =>
          table.evaluate(
            (node) =>
              node
                .getAnimations({ subtree: true })
                .filter((animation) => animation.playState === "running")
                .length,
          ),
        )
        .toBe(0);
      const handle = table.locator(
        `[data-arrangement-id="${id}"] .task-drag-handle`,
      );
      const destination = table
        .locator(`[data-arrangement-id="${target}"] td`)
        .first();
      if (width >= 768) {
        await handle.dragTo(destination, { targetPosition: { x: 4, y: 4 } });
      } else {
        // Fully visible mobile fields make each row taller than one screen.
        // Keep the drag held while scrolling the destination into view.
        await handle.scrollIntoViewIfNeeded();
        const from = (await handle.boundingBox())!;
        await page.mouse.move(
          from.x + from.width / 2,
          from.y + from.height / 2,
        );
        await page.mouse.down();
        await page.mouse.move(
          from.x + from.width / 2,
          from.y + from.height / 2 + 12,
          { steps: 3 },
        );
        await expect(handle.locator("xpath=ancestor::tr")).toHaveClass(
          /task-order-chosen/,
        );
        await destination.evaluate((node) =>
          window.scrollBy({
            top: node.getBoundingClientRect().top - 400,
            behavior: "instant",
          }),
        );
        const to = (await destination.boundingBox())!;
        await page.mouse.move(to.x + 4, to.y + 4, { steps: 8 });
        await page.mouse.up();
      }
    };
    await expect(page.locator(".el-loading-mask")).toHaveCount(0);
    await move("b", "a");
    await expect
      .poll(contents)
      .toEqual(["工作b", "工作a", "工作c", "工作review-future"]);
    const first = table.locator('[data-arrangement-id="b"] .task-drag-handle');
    await first.focus();
    await first.press("ArrowDown");
    await expect
      .poll(contents)
      .toEqual(["工作a", "工作b", "工作c", "工作review-future"]);
    await move("carried", "b");
    await expect
      .poll(contents)
      .toEqual(["工作a", "工作review-future", "工作b", "工作c"]);
    await move("b", "carried");
    await expect
      .poll(contents)
      .toEqual(["工作a", "工作b", "工作review-future", "工作c"]);
    await page.getByRole("button", { name: "保存草稿", exact: true }).click();
    const saved = result.draft(),
      current = saved.items.filter((x: any) => x.section === "new");
    expect(current.map((x: any) => x.id)).toEqual(["a", "b", "carried", "c"]);
    for (const original of result.initial.items)
      expect(saved.items.find((x: any) => x.id === original.id)).toMatchObject(
        original,
      );
    expect(saved.items.filter((x: any) => x.section === "review")).toEqual(
      result.initial.items.filter((x: any) => x.section === "review"),
    );
    expect(saved.items).toHaveLength(result.initial.items.length);
    await page.reload();
    await expect(rows).toHaveCount(4);
    expect(await contents()).toEqual([
      "工作a",
      "工作b",
      "工作review-future",
      "工作c",
    ]);
    await expect(page.locator(".el-loading-mask")).toHaveCount(0);
    await table.evaluate((node) => node.scrollIntoView({ block: "start" }));
    await page.screenshot({
      path: `${evidence}/task-order-${width}.png`,
      fullPage: false,
    });
    await page.getByRole("button", { name: "预览并发布", exact: true }).click();
    await expect.poll(() => result.previews.length).toBe(1);
    expect(
      result.previews[0].items
        .filter((x: any) => x.section === "new")
        .map((x: any) => x.id),
    ).toEqual(["a", "b", "carried", "c"]);
    fs.writeFileSync(
      `${evidence}/saved-preview-${width}.json`,
      JSON.stringify(result.previews[0], null, 2),
    );
    expect(result.errors).toEqual([]);
  });

  test(`attendees and both notification channels share order at ${width}px`, async ({
    page,
  }, testInfo) => {
    await page.setViewportSize({ width, height: 950 });
    const result = await setup(page, false, false, {
      draft: {
        attendee_ids: ["u7", "u2", "u9"],
        recorder_id: "u12",
        online_attendee_ids: ["u9"],
      },
    });
    await page.goto("/meetings/check-order");
    await page.getByRole("button", { name: "预览并发布", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "确认发布会议纪要" });
    const names = () =>
      dialog
        .locator(".people-sortable-tag .people-select-name")
        .allTextContents();
    await expect(dialog.locator(".pdf-preview-loading")).toHaveCount(0);
    await expect(
      dialog.locator(".pdf-preview-sheet canvas").first(),
    ).toBeVisible();
    await expect.poll(names).toEqual(["主持甲", "员工7", "员工2", "员工9"]);
    await expect(dialog.locator(".mail-recipient-tag")).toHaveText([
      "主持甲 · person0@example.test",
      "员工7 · person7@example.test",
      "员工2 · person2@example.test",
      "员工9 · person9@example.test",
    ]);
    await expect(dialog.locator(".mail-cc-tag")).toHaveCount(0);
    await dialog
      .getByRole("button", { name: "拖动员工9调整顺序", exact: true })
      .press("ArrowLeft");
    await expect.poll(names).toEqual(["主持甲", "员工7", "员工9", "员工2"]);
    await expect
      .poll(() => result.previews.at(-1)?.attendee_ids)
      .toEqual(["u7", "u9", "u2"]);
    await expect(dialog.locator(".pdf-preview-loading")).toHaveCount(0);
    await expect(
      dialog.locator(".pdf-preview-sheet canvas").first(),
    ).toBeVisible();
    await expect(dialog.locator(".mail-recipient-tag")).toHaveText([
      "主持甲 · person0@example.test",
      "员工7 · person7@example.test",
      "员工9 · person9@example.test",
      "员工2 · person2@example.test",
    ]);
    const input = dialog.getByRole("combobox", {
      name: "通知接收人",
      exact: true,
    });
    await input.scrollIntoViewIfNeeded();
    await input.fill("person12@example.test");
    await page.getByRole("option", { name: "员工12", exact: true }).click();
    await expect(dialog.locator(".mail-recipient-tag").last()).toHaveText(
      "员工12 · person12@example.test",
    );
    const cc = dialog.getByRole("combobox", {
      name: "邮件抄送人或邮箱",
      exact: true,
    });
    await cc.fill("外部人员 <External＠EXAMPLE.com>");
    await cc.press("Enter");
    await expect(dialog.locator(".mail-cc-tag")).toContainText(
      "External@example.com",
    );
    await input.scrollIntoViewIfNeeded();
    await dialog.screenshot({
      path: testInfo.outputPath(`recipients-${width}.png`),
    });
    await dialog.getByRole("button", { name: "返回编辑", exact: true }).click();
    await page.getByRole("button", { name: "保存草稿", exact: true }).click();
    expect(result.draft().recipient_ids).toEqual([
      "host",
      "u7",
      "u9",
      "u2",
      "u12",
    ]);
    expect(result.draft().attendee_ids).toEqual(["u7", "u9", "u2"]);
    expect(result.draft().cc_ids).toEqual([]);
    expect(result.draft().cc_emails).toEqual(["External@example.com"]);
    await page.reload();
    await page.getByRole("button", { name: "预览并发布", exact: true }).click();
    await expect
      .poll(names)
      .toEqual(["主持甲", "员工7", "员工9", "员工2", "员工12"]);
    expect(result.errors).toEqual([]);
  });

  test(`missing contact stays visible instead of disappearing at ${width}px`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 950 });
    await setup(page, false, false, {
      draft: { attendee_ids: ["u2"] },
      missingEmail: "u2",
    });
    await page.goto("/meetings/check-order");
    await page.getByRole("button", { name: "预览并发布", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "确认发布会议纪要" });
    await expect(dialog.locator(".mail-recipient-tag").last()).toContainText(
      "员工2 · 未配置邮箱",
    );
    await expect(dialog).toContainText("员工2尚未配置邮箱");
    await expect(
      dialog.locator(".people-sortable-tag .people-select-name"),
    ).toHaveText(["主持甲", "员工2"]);
  });
}

test("published viewing has no ordering controls and creates no draft", async ({
  page,
}) => {
  const result = await setup(page, true);
  await page.goto("/meetings/check-order");
  await expect(page.locator(".meeting-plan-table tbody tr")).toHaveCount(4);
  await expect(page.locator(".task-drag-handle")).toHaveCount(0);
  expect(result.writes).toEqual([]);
  expect(result.errors).toEqual([]);
});

test.describe("touch dragging", () => {
  test.use({
    hasTouch: true,
    isMobile: true,
    viewport: { width: 390, height: 900 },
  });
  test("holding the handle moves a task across multiple rows and keeps input editing intact", async ({
    page,
  }) => {
    const result = await setup(page);
    await page.goto("/meetings/check-order");
    const table = page.locator(".meeting-plan-table");
    await expect(table.locator(".task-drag-handle")).toHaveCount(4);
    await expect(page.locator(".el-loading-mask")).toHaveCount(0);
    const handle = table.locator('[data-arrangement-id="c"] .task-drag-handle');
    await handle.scrollIntoViewIfNeeded();
    const from = (await handle.boundingBox())!;
    const x = from.x + from.width / 2,
      startY = from.y + from.height / 2;
    const client = await page.context().newCDPSession(page);
    await client.send("Input.dispatchTouchEvent", {
      type: "touchStart",
      touchPoints: [{ x, y: startY, id: 1 }],
    });
    await expect(table.locator('[data-arrangement-id="c"]')).toHaveClass(
      /task-order-chosen/,
    );
    await client.send("Input.dispatchTouchEvent", {
      type: "touchMove",
      touchPoints: [{ x, y: startY - 12, id: 1 }],
    });
    await table
      .locator('[data-arrangement-id="a"] td')
      .first()
      .evaluate((node) =>
        window.scrollBy({
          top: node.getBoundingClientRect().top - 400,
          behavior: "instant",
        }),
      );
    const destination = (await table
      .locator('[data-arrangement-id="a"] td')
      .first()
      .boundingBox())!;
    const endY = destination.y + 4;
    for (let step = 1; step <= 12; step++) {
      await client.send("Input.dispatchTouchEvent", {
        type: "touchMove",
        touchPoints: [{ x, y: startY + ((endY - startY) * step) / 12, id: 1 }],
      });
    }
    await expect
      .poll(() =>
        table
          .locator("tbody > tr[data-arrangement-id]:not(.sortable-fallback)")
          .evaluateAll((nodes) =>
            nodes.map((node) => (node as HTMLElement).dataset.arrangementId),
          ),
      )
      .toEqual(["c", "a", "b", "carried"]);
    await client.send("Input.dispatchTouchEvent", {
      type: "touchEnd",
      touchPoints: [],
    });
    await client.detach();
    await expect(table.locator(".sortable-fallback")).toHaveCount(0);
    await table
      .locator('[data-arrangement-id="c"]')
      .getByRole("textbox", { name: "工作内容及要求", exact: true })
      .fill("触屏排序后修改内容");
    await page.getByRole("button", { name: "保存草稿", exact: true }).click();
    const current = result
      .draft()
      .items.filter((item: any) => item.section === "new");
    expect(current.map((item: any) => item.id)).toEqual([
      "c",
      "a",
      "b",
      "carried",
    ]);
    expect(current[0].content).toBe("触屏排序后修改内容");
    expect(
      result.draft().items.filter((item: any) => item.section === "review"),
    ).toEqual(
      result.initial.items.filter((item: any) => item.section === "review"),
    );
    expect(result.errors).toEqual([]);
  });
});
