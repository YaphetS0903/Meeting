import { expect, test, type Page } from "@playwright/test";
import { fileURLToPath } from "node:url";

const users = [
  "袁可炜",
  "郑军鹏",
  "陈浩东",
  "金丽君",
  "顾佳益",
  "马军平",
  "强健林",
  "赵小龙",
].map((name, i) => ({
  id: `p${i}`,
  name,
  active: true,
  department_id: "dept",
  email: `p${i}@example.test`,
}));

async function setup(page: Page) {
  let draft: any = {
    title: "线上参会功能核对",
    department_id: "dept",
    kind: "department",
    host_id: "p0",
    recorder_id: "p3",
    meeting_date: "2026-11-12",
    attendee_ids: ["p1", "p2"],
    attachment_ids: [],
    recipient_ids: [],
    cc_ids: [],
    cc_emails: [],
    absentees: "",
    items: [],
  };
  let version = 1;
  const errors: string[] = [];
  const previews: any[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.addInitScript(() =>
    sessionStorage.setItem("token", "online-attendance-test"),
  );
  await page.route("**/api/**", async (route) => {
    const request = route.request(),
      path = new URL(request.url()).pathname;
    let body: any;
    if (path === "/api/me") body = { ...users[0], role: "admin" };
    else if (path === "/api/auth/options") body = { wecom: false };
    else if (path === "/api/directory")
      body = {
        users,
        departments: [{ id: "dept", name: "信息部" }],
        series: [],
      };
    else if (path === "/api/meeting-people") body = { users };
    else if (path === "/api/meetings/online/preview") {
      previews.push(structuredClone(draft));
      return route.fulfill({
        path: fileURLToPath(
          new URL("./fixtures/preview-minutes.pdf", import.meta.url),
        ),
        contentType: "application/pdf",
      });
    } else if (path === "/api/meetings/online") {
      if (request.method() === "PUT") {
        expect(request.postDataJSON().expected_version).toBe(version);
        draft = request.postDataJSON().draft;
        version++;
      }
      body = {
        id: "online",
        draft,
        draft_version: version,
        published_version: 0,
        can_edit: true,
        has_draft: true,
        historical: false,
        attachments: [],
        versions: [],
        assessments: {},
      };
    } else
      throw new Error(`Unexpected API request: ${request.method()} ${path}`);
    await route.fulfill({ json: body });
  });
  await page.goto("/meetings/online");
  const open = async () => {
    await page
      .locator(".attendee-card")
      .getByRole("button", { name: "＋ 新增" })
      .click();
    return page.getByRole("dialog", { name: "新增或调整参会人员" });
  };
  return { open, draft: () => draft, previews, errors };
}

for (const width of [1366, 390]) {
  test(`online participants can be added directly, persisted and shown once at ${width}px`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 900 });
    const result = await setup(page);
    const dialog = await result.open();
    const input = dialog.getByRole("combobox", {
      name: "线上参会人员",
      exact: true,
    });
    await dialog
      .locator(".el-form-item")
      .filter({ hasText: "线上参会人员" })
      .locator(".el-select__wrapper")
      .click();
    for (const name of ["袁可炜", "郑军鹏", "陈浩东"])
      await expect(
        page.getByRole("option", { name, exact: true }),
      ).toBeVisible();
    await expect(
      page.getByRole("option", { name: "金丽君", exact: true }),
    ).toBeVisible();
    await page.getByRole("option", { name: "袁可炜", exact: true }).click();
    await page.getByRole("option", { name: "郑军鹏", exact: true }).click();
    await page.getByRole("option", { name: "金丽君", exact: true }).click();
    await input.press("Escape");
    await expect(
      page.getByRole("option", { name: "陈浩东", exact: true }),
    ).toBeHidden();
    const onsite = dialog
      .locator(".el-form-item")
      .filter({ hasText: "现场参会人员" });
    await expect(onsite.locator(".people-select-name")).toHaveText(["陈浩东"]);
    await expect(dialog.locator(".participant-dialog-host")).toHaveCount(0);
    await expect(dialog.locator(".participant-dialog-total")).toContainText(
      "共 4 人",
    );
    await page.screenshot({
      path: `../../.validation/participant-groups-order-20261112/dialog-${width}.png`,
    });
    await dialog.getByRole("button", { name: "确认", exact: true }).click();
    await expect(page.locator(".attendee-person")).toHaveCount(4);
    await expect(
      page.locator(".attendee-person").filter({ hasText: "袁可炜" }),
    ).toContainText("主持人 · 线上");
    await expect(
      page.locator(".attendee-person").filter({ hasText: "郑军鹏" }),
    ).toContainText("参会人员 · 线上");
    await expect(
      page.locator(".attendee-person").filter({ hasText: "金丽君" }),
    ).toContainText("参会人员 · 线上");
    await page.getByRole("button", { name: "保存草稿", exact: true }).click();
    await expect
      .poll(() => result.draft().online_attendee_ids)
      .toEqual(["p0", "p1", "p3"]);
    expect(result.draft().attendee_ids).toEqual(["p2", "p1", "p3"]);
    await page.reload();
    await expect(
      page.locator(".attendee-person").filter({ hasText: "郑军鹏" }),
    ).toContainText("线上");
    await page.getByRole("button", { name: "预览并发布", exact: true }).click();
    await expect.poll(() => result.previews.length).toBe(1);
    expect(result.previews[0].online_attendee_ids).toEqual(["p0", "p1", "p3"]);
    expect(result.errors).toEqual([]);
  });
}

test("pasting online names adds them without preselecting the attendee list", async ({
  page,
}) => {
  const result = await setup(page);
  const dialog = await result.open();
  const input = dialog.getByRole("combobox", {
    name: "线上参会人员",
    exact: true,
  });
  await input.evaluate((node) => {
    const data = new DataTransfer();
    data.setData("text/plain", "顾佳益、马军平、强健林、赵小龙、顾佳益");
    node.dispatchEvent(
      new ClipboardEvent("paste", {
        clipboardData: data,
        bubbles: true,
        cancelable: true,
      }),
    );
  });
  await expect(
    dialog
      .locator(".el-form-item")
      .filter({ hasText: "线上参会人员" })
      .locator(".el-tag"),
  ).toHaveCount(4);
  await expect(dialog.locator(".participant-dialog-total")).toContainText(
    "共 7 人",
  );
  await expect(page.getByText(/未匹配或不在可选范围/)).toHaveCount(0);
  await dialog.getByRole("button", { name: "确认", exact: true }).click();
  await page.getByRole("button", { name: "保存草稿", exact: true }).click();
  await expect
    .poll(() => result.draft().online_attendee_ids)
    .toEqual(["p4", "p5", "p6", "p7"]);
  expect(result.draft().attendee_ids).toEqual([
    "p1",
    "p2",
    "p4",
    "p5",
    "p6",
    "p7",
  ]);
  expect(result.errors).toEqual([]);
});

test("removing online attendees removes attendance, while cancelling keeps saved selections", async ({
  page,
}) => {
  const result = await setup(page);
  let dialog = await result.open();
  const online = dialog.getByRole("combobox", {
    name: "线上参会人员",
    exact: true,
  });
  await dialog
    .locator(".el-form-item")
    .filter({ hasText: "线上参会人员" })
    .locator(".el-select__wrapper")
    .click();
  await page.getByRole("option", { name: "郑军鹏", exact: true }).click();
  await online.press("Escape");
  await dialog.getByRole("button", { name: "取消", exact: true }).click();
  dialog = await result.open();
  await expect(
    dialog
      .locator(".el-form-item")
      .filter({ hasText: "线上参会人员" })
      .locator(".el-tag"),
  ).toHaveCount(0);
  await dialog
    .locator(".el-form-item")
    .filter({ hasText: "线上参会人员" })
    .locator(".el-select__wrapper")
    .click();
  await page.getByRole("option", { name: "郑军鹏", exact: true }).click();
  await online.press("Escape");
  const attendees = dialog
    .locator(".el-form-item")
    .filter({ hasText: "线上参会人员" });
  await attendees
    .locator(".el-tag")
    .filter({ hasText: "郑军鹏" })
    .locator(".el-tag__close")
    .click();
  await expect(
    dialog
      .locator(".el-form-item")
      .filter({ hasText: "线上参会人员" })
      .locator(".el-tag"),
  ).toHaveCount(0);
  await dialog.getByRole("button", { name: "确认", exact: true }).click();
  await page.getByRole("button", { name: "保存草稿", exact: true }).click();
  await expect.poll(() => result.draft().online_attendee_ids).toEqual([]);
  expect(result.draft().attendee_ids).toEqual(["p2"]);
  expect(result.errors).toEqual([]);
});

test("switching between onsite and online moves people without duplicates, including the host", async ({
  page,
}) => {
  async function select(field: ReturnType<typeof page.locator>, name: string) {
    const input = field.getByRole("combobox");
    await input.fill(name);
    const listId = await input.getAttribute("aria-controls");
    await page
      .locator(`[id="${listId}"]`)
      .getByRole("option", { name, exact: true })
      .click();
  }
  const result = await setup(page);
  let dialog = await result.open();
  const onsite = () =>
    dialog.locator(".el-form-item").filter({ hasText: "现场参会人员" });
  const online = () =>
    dialog.locator(".el-form-item").filter({ hasText: "线上参会人员" });
  await select(online(), "郑军鹏");
  await online().getByRole("combobox").press("Escape");
  await expect(onsite().locator(".people-select-name")).toHaveText(["陈浩东"]);
  await expect(online().locator(".people-select-name")).toHaveText(["郑军鹏"]);
  await select(onsite(), "郑军鹏");
  await onsite().getByRole("combobox").press("Escape");
  await expect(online().locator(".people-select-name")).toHaveCount(0);
  await expect(onsite().locator(".people-select-name")).toHaveText([
    "陈浩东",
    "郑军鹏",
  ]);
  await select(online(), "袁可炜");
  await online().getByRole("combobox").press("Escape");
  await expect(dialog.locator(".participant-dialog-host")).toHaveCount(0);
  await expect(dialog.locator(".participant-dialog-total")).toContainText(
    "共 3 人",
  );
  await dialog.getByRole("button", { name: "确认", exact: true }).click();
  await page.getByRole("button", { name: "保存草稿", exact: true }).click();
  await expect.poll(() => result.draft().online_attendee_ids).toEqual(["p0"]);
  await page.reload();
  dialog = await result.open();
  await expect(onsite().locator(".people-select-name")).toHaveText([
    "陈浩东",
    "郑军鹏",
  ]);
  await expect(online().locator(".people-select-name")).toHaveText(["袁可炜"]);
  await select(onsite(), "袁可炜");
  await onsite().getByRole("combobox").press("Escape");
  await expect(online().locator(".people-select-name")).toHaveCount(0);
  await expect(dialog.locator(".participant-dialog-host")).toHaveCount(1);
  await expect(dialog.locator(".participant-dialog-total")).toContainText(
    "共 3 人",
  );
  expect(result.errors).toEqual([]);
});

for (const width of [1366, 390]) {
  test(`attendee drag order and keyboard order survive saving and reopening at ${width}px`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 900 });
    const result = await setup(page);
    let dialog = await result.open();
    const onsite = () =>
      dialog.locator(".el-form-item").filter({ hasText: "现场参会人员" });
    await onsite()
      .getByRole("combobox")
      .evaluate((node) => {
        const data = new DataTransfer();
        data.setData("text/plain", "金丽君、顾佳益");
        node.dispatchEvent(
          new ClipboardEvent("paste", {
            clipboardData: data,
            bubbles: true,
            cancelable: true,
          }),
        );
      });
    await onsite().getByRole("button", { name: "拖动顾佳益调整顺序" }).hover();
    const start = await onsite()
      .getByRole("button", { name: "拖动顾佳益调整顺序" })
      .boundingBox();
    const end = await onsite()
      .locator(".people-sortable-tag")
      .filter({ hasText: "郑军鹏" })
      .boundingBox();
    expect(start).toBeTruthy();
    expect(end).toBeTruthy();
    await page.mouse.move(
      start!.x + start!.width / 2,
      start!.y + start!.height / 2,
    );
    await page.mouse.down();
    await page.mouse.move(start!.x + 8, start!.y + 12, { steps: 3 });
    await page.waitForTimeout(180);
    await page.mouse.move(end!.x + 3, end!.y + end!.height / 2, {
      steps: 15,
    });
    await page.waitForTimeout(200);
    await page.mouse.move(end!.x + 4, end!.y + end!.height / 2);
    await page.waitForTimeout(200);
    await page.mouse.up();
    await expect(onsite().locator(".people-select-name")).toHaveText([
      "顾佳益",
      "郑军鹏",
      "陈浩东",
      "金丽君",
    ]);
    await onsite()
      .getByRole("button", { name: "拖动金丽君调整顺序" })
      .press("ArrowLeft");
    await expect(onsite().locator(".people-select-name")).toHaveText([
      "顾佳益",
      "郑军鹏",
      "金丽君",
      "陈浩东",
    ]);
    await dialog.getByRole("button", { name: "确认", exact: true }).click();
    await page.getByRole("button", { name: "保存草稿", exact: true }).click();
    await expect
      .poll(() => result.draft().attendee_ids)
      .toEqual(["p4", "p1", "p3", "p2"]);
    await page.reload();
    dialog = await result.open();
    await expect(onsite().locator(".people-select-name")).toHaveText([
      "顾佳益",
      "郑军鹏",
      "金丽君",
      "陈浩东",
    ]);
    await onsite()
      .getByRole("button", { name: "拖动顾佳益调整顺序" })
      .press("ArrowRight");
    await dialog.getByRole("button", { name: "取消", exact: true }).click();
    dialog = await result.open();
    await expect(onsite().locator(".people-select-name")).toHaveText([
      "顾佳益",
      "郑军鹏",
      "金丽君",
      "陈浩东",
    ]);
    expect(result.errors).toEqual([]);
  });
}
