import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const nativeDialogTitle = "派发本次会议的企微待办";
const publicationTitle = "确认发布会议纪要";
const firstId = "isolated-publication-first";
const secondId = "isolated-publication-second";

function previewPdf() {
  const text = "BT /F1 12 Tf 30 700 Td (Isolated publication state) Tj ET";
  const objects = [
    "<< /Type /Catalog /Pages 2 0 R >>",
    "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
    "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
    "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    `<< /Length ${Buffer.byteLength(text)} >>\nstream\n${text}\nendstream`,
  ];
  let source = "%PDF-1.4\n";
  const offsets = [0];
  for (const [index, value] of objects.entries()) {
    offsets.push(Buffer.byteLength(source));
    source += `${index + 1} 0 obj\n${value}\nendobj\n`;
  }
  const xref = Buffer.byteLength(source);
  source += `xref\n0 6\n0000000000 65535 f \n`;
  source += offsets
    .slice(1)
    .map((offset) => `${String(offset).padStart(10, "0")} 00000 n \n`)
    .join("");
  source += `trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`;
  return Buffer.from(source);
}

async function fixture(
  page: Page,
  options: { startNew?: boolean; historical?: boolean } = {},
) {
  const host = {
    id: "isolated-state-host",
    name: "隔离主持人",
    active: true,
    is_admin: false,
    can_assign_tasks: false,
    can_oversee_tasks: false,
    department_id: "isolated-state-department",
    position: "工程师",
    wecom_id: "isolated-state-wecom",
    email: "host@example.test",
  };
  const owner = { ...host, id: "isolated-state-owner", name: "隔离责任人" };
  const task = {
    id: "isolated-first-item",
    section: "new",
    category: "",
    content: "第一场任务",
    owner_ids: [owner.id],
    owner_names: [owner.name],
    due_date: "2026-11-01",
    status: "未到期",
    verifier_id: host.id,
    deliverable: "",
    notes: "",
    requirements: "",
    exclusion_reason: "",
    task_id: "isolated-first-task",
    task_version: 1,
  };
  const draft = {
    title: "第一场隔离会议",
    department_id: host.department_id,
    series_id: null,
    kind: "department",
    meeting_date: "2026-10-27",
    meeting_time: "14:00",
    location: "隔离会议室",
    host_id: host.id,
    recorder_id: host.id,
    reviewer_id: null,
    attendee_ids: [host.id, owner.id],
    absentees: "",
    decisions: "",
    items: [task],
    attachment_ids: [],
    recipient_ids: [],
    cc_ids: [],
  };
  const records: Record<string, any> = {
    [firstId]: {
      draft: structuredClone(draft),
      draft_version: 1,
      published_version: 0,
    },
  };
  const controls = {
    publishes: [] as string[],
    publicationReasons: [] as string[],
    notificationChoices: [] as boolean[],
    plans: [] as string[],
    saves: [] as string[],
    nativeWrites: [] as string[],
    unexpected: [] as string[],
    errors: [] as string[],
    failPublishedDetailOnce: "",
    failedDetailReads: [] as string[],
    creates: 0,
    draft: () => structuredClone(records[firstId].draft),
    saveFailure: false,
    previewBody: previewPdf(),
    previewFailure: null as null | {
      status: number;
      body: string;
      contentType?: string;
    },
    emptyPublishResponse: false,
    skipPublishCommit: false,
  };
  const meeting = (id: string) => {
    const record = records[id];
    return {
      id,
      ...record,
      title: record.draft.title,
      series_id: null,
      historical: !!options.historical,
      has_draft: record.has_draft ?? !record.published_version,
      can_edit: true,
      can_continue: !!record.published_version,
      can_delete: false,
      versions: record.published_version
        ? [{ number: 1, snapshot: record.draft }]
        : [],
      attachments: [],
      notifications: [],
      task_notifications: [],
      assessments: {},
    };
  };
  await page.addInitScript(() => {
    if (window === window.top) {
      sessionStorage.setItem("token", "isolated-publication-token");
      // These cases exercise publication state, not Chrome's embedded PDF engine.
    }
  });
  page.on("pageerror", (error) => controls.errors.push(error.message));
  await page.route("**/api/**", async (route) => {
    const request = route.request(),
      url = new URL(request.url()),
      pathname = url.pathname;
    let body: any;
    if (pathname === "/api/me") body = host;
    else if (pathname === "/api/auth/options") body = { wecom: false };
    else if (pathname === "/api/meeting-people")
      body = { users: [host, owner] };
    else if (pathname === "/api/directory")
      body = {
        users: [host, owner],
        departments: [{ id: host.department_id, name: "隔离部门" }],
        series: [],
      };
    else if (pathname === "/api/meetings" && request.method() === "POST") {
      controls.creates++;
      records[firstId].draft = request.postDataJSON();
      body = meeting(firstId);
    } else {
      const match =
        /^\/api\/meetings\/([^/]+)(?:\/(preview|publish|continue|revision|native-todos))?$/.exec(
          pathname,
        );
      const id = match?.[1],
        operation = match?.[2];
      if (!id || !records[id]) {
        if (pathname.startsWith("/api/tasks/") && request.method() !== "GET")
          controls.nativeWrites.push(pathname);
        else controls.unexpected.push(`${request.method()} ${pathname}`);
        await route.fulfill({
          status: 404,
          json: { detail: "隔离请求未实现" },
        });
        return;
      }
      if (operation === "preview") {
        if (controls.previewFailure) {
          await route.fulfill(controls.previewFailure);
          return;
        }
        await route.fulfill({
          contentType: "application/pdf",
          body: controls.previewBody,
        });
        return;
      } else if (operation === "publish") {
        controls.publishes.push(id);
        controls.publicationReasons.push(request.postDataJSON().reason);
        controls.notificationChoices.push(request.postDataJSON().notify);
        if (!controls.skipPublishCommit) {
          records[id].published_version = 1;
          records[id].has_draft = false;
        }
        if (controls.emptyPublishResponse) {
          await route.fulfill({
            status: 200,
            contentType: "application/json",
            body: "",
          });
          return;
        }
        body = { published_version: 1 };
      } else if (operation === "revision") {
        records[id].has_draft = true;
        body = meeting(id);
      } else if (operation === "continue") {
        records[secondId] = {
          draft: {
            ...structuredClone(records[id].draft),
            title: "第二场接续会议",
            items: records[id].draft.items.map((item: any) => ({
              ...item,
              section: "review",
            })),
          },
          draft_version: 1,
          published_version: 0,
        };
        body = meeting(secondId);
      } else if (operation === "native-todos") {
        controls.plans.push(id);
        body = {
          meeting_id: id,
          published_version: 1,
          enabled: true,
          can_confirm: true,
          reason: "",
          excluded_tasks: [],
          tasks: records[id].draft.items
            .filter((item: any) => item.section === "new")
            .map((item: any, index: number) => ({
              id: `${id}-task-${index}`,
              code: `RW-ISOLATED-${index}`,
              content: item.content,
              due_date: item.due_date,
              owner_names: [owner.name],
              version: 1,
              source_meeting_id: id,
              native_todo: {
                enabled: true,
                can_create: true,
                is_issuer: true,
                source: "meeting",
                reason: "",
                pending_attempt: null,
                link: null,
                warning: "",
                message_state: "skipped",
              },
            })),
        };
      } else {
        if (
          request.method() === "GET" &&
          records[id].published_version &&
          controls.failPublishedDetailOnce === id
        ) {
          controls.failPublishedDetailOnce = "";
          controls.failedDetailReads.push(id);
          await route.fulfill({
            status: 503,
            json: { detail: "隔离模拟：发布成功后详情刷新暂不可用" },
          });
          return;
        }
        if (request.method() === "PUT") {
          controls.saves.push(id);
          if (controls.saveFailure) {
            await route.fulfill({
              status: 503,
              json: { detail: "模拟保存失败" },
            });
            return;
          }
          records[id].draft = request.postDataJSON().draft;
          records[id].draft_version++;
        }
        body = meeting(id);
      }
    }
    await route.fulfill({ json: body });
  });
  await page.goto(`/meetings/${options.startNew ? "new" : firstId}`);
  await expect(
    page.getByRole("button", { name: "预览并发布", exact: true }),
  ).toBeVisible();
  return controls;
}

async function preview(page: Page) {
  await page.getByRole("button", { name: "预览并发布", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: publicationTitle });
  await expect(
    dialog.getByRole("button", { name: "确认发布", exact: true }),
  ).toBeEnabled();
  return dialog;
}

async function publish(page: Page) {
  const dialog = page.getByRole("dialog", { name: publicationTitle });
  await dialog.getByRole("button", { name: "确认发布", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "持续新建", exact: true }),
  ).toBeVisible();
}

async function continueMeeting(page: Page) {
  await page.getByRole("button", { name: "持续新建", exact: true }).click();
  await page
    .getByRole("dialog", { name: "持续新建", exact: true })
    .getByRole("button", { name: "确认新建", exact: true })
    .click();
  await expect(page).toHaveURL(new RegExp(`/meetings/${secondId}$`));
  await expect(
    page.getByRole("button", { name: "＋ 新增工作", exact: true }),
  ).toBeVisible();
}

async function addSecondTask(page: Page) {
  await page.getByRole("button", { name: "＋ 新增工作", exact: true }).click();
  const content = page.getByRole("textbox", {
    name: "工作内容及要求",
    exact: true,
  });
  await content.fill("第二场新增任务");
  const row = page.locator("tr").filter({ has: content });
  await row
    .getByRole("combobox", { name: "责任人", exact: true })
    .fill("隔离责任人");
  await page.getByRole("option", { name: "隔离责任人", exact: true }).click();
  await content.click();
  await row
    .getByRole("combobox", { name: "计划完成时间", exact: true })
    .fill("2026-11-02");
  await content.click();
}

function assertClean(controls: Awaited<ReturnType<typeof fixture>>) {
  expect(controls.nativeWrites).toEqual([]);
  expect(controls.unexpected).toEqual([]);
  expect(controls.errors).toEqual([]);
}

for (const width of [1366, 390]) {
  test(`复制图片在受限浏览器中显示完整多页长图，支持原图下载和刷新 ${width}`, async ({
    page,
  }, testInfo) => {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 900 });
    await page.addInitScript(() =>
      Object.defineProperty(navigator, "clipboard", {
        value: undefined,
        configurable: true,
      }),
    );
    const controls = await fixture(page);
    controls.previewBody = readFileSync(
      fileURLToPath(new URL("./fixtures/preview-minutes.pdf", import.meta.url)),
    );
    const dialog = await preview(page);
    const viewer = dialog.getByRole("region", { name: "发布前 PDF 纪要预览" });
    const copy = viewer.getByRole("button", { name: "复制图片", exact: true });
    await expect(copy).toBeEnabled();
    const dimensions = await viewer
      .locator(".pdf-preview-sheet")
      .evaluateAll((nodes) =>
        nodes.map((node) =>
          (node as HTMLElement).style.aspectRatio.split("/").map(Number),
        ),
      );
    expect(dimensions.length).toBeGreaterThan(1);
    const ratio =
      dimensions.reduce((sum, dimension) => sum + dimension[1], 0) /
      Math.max(...dimensions.map((dimension) => dimension[0]));
    await copy.scrollIntoViewIfNeeded();
    await dialog.screenshot({
      path: testInfo.outputPath(`copy-button-${width}.png`),
    });
    const saves = controls.saves.length;
    await copy.click();
    const image = viewer.getByRole("img", {
      name: "完整会议纪要，可右键或长按复制",
      exact: true,
    });
    await expect(image).toBeVisible();
    await expect(viewer.getByRole("status")).toContainText(
      "请右键图片选择“复制图片”",
    );
    const size = await image.evaluate((node: HTMLImageElement) => ({
      width: node.naturalWidth,
      height: node.naturalHeight,
      src: node.src,
    }));
    expect(size.width).toBeGreaterThanOrEqual(1000);
    expect(Math.abs(size.height / size.width - ratio)).toBeLessThan(0.01);
    expect(
      await image.evaluate((node) => {
        const event = new MouseEvent("contextmenu", {
          bubbles: true,
          cancelable: true,
          button: 2,
        });
        node.dispatchEvent(event);
        return event.defaultPrevented;
      }),
    ).toBe(false);
    await dialog.screenshot({
      path: testInfo.outputPath(`copy-full-image-${width}.png`),
    });
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth),
    ).toBeLessThanOrEqual(width);
    const downloadEvent = page.waitForEvent("download");
    await viewer.getByRole("link", { name: "下载图片", exact: true }).click();
    const download = await downloadEvent;
    expect(download.suggestedFilename()).toBe("第一场隔离会议.png");
    await download.saveAs(testInfo.outputPath("full-minutes.png"));
    expect(
      readFileSync(testInfo.outputPath("full-minutes.png"))
        .subarray(0, 8)
        .toString("hex"),
    ).toBe("89504e470d0a1a0a");
    expect(controls.saves.length).toBe(saves);
    expect(controls.publishes).toEqual([]);

    controls.previewBody = previewPdf();
    await dialog
      .getByRole("button", { name: "刷新 PDF 预览", exact: true })
      .click();
    await expect(image).toHaveCount(0);
    await expect(copy).toBeEnabled();
    await expect(viewer.locator(".pdf-preview-sheet")).toHaveCount(1);
    expect(
      await page.evaluate(
        (src) =>
          fetch(src).then(
            () => true,
            () => false,
          ),
        size.src,
      ),
    ).toBe(false);
    await copy.click();
    await expect(image).toBeVisible();
    expect(
      await image.evaluate((node: HTMLImageElement) => node.naturalHeight),
    ).toBeLessThan(size.height);
    assertClean(controls);
  });
}

test("复制图片将完整PNG交给剪贴板并保留点击手势，不触发发布", async ({
  page,
}) => {
  await page.addInitScript(() =>
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: {
        write: async (items: ClipboardItem[]) => {
          const active = navigator.userActivation.isActive;
          const blob = await items[0].getType("image/png");
          const image = await createImageBitmap(blob);
          (window as any).__copiedImage = {
            active,
            type: blob.type,
            bytes: blob.size,
            width: image.width,
            height: image.height,
          };
          image.close();
        },
      },
    }),
  );
  const controls = await fixture(page);
  controls.previewBody = readFileSync(
    fileURLToPath(new URL("./fixtures/preview-minutes.pdf", import.meta.url)),
  );
  const dialog = await preview(page);
  const viewer = dialog.getByRole("region", { name: "发布前 PDF 纪要预览" });
  const copy = viewer.getByRole("button", { name: "复制图片", exact: true });
  await expect(copy).toBeEnabled();
  await copy.click();
  await expect(
    page.getByText("完整纪要图片已复制，可直接粘贴。", { exact: true }),
  ).toBeVisible();
  const result = await page.evaluate(() => (window as any).__copiedImage);
  expect(result).toMatchObject({ active: true, type: "image/png" });
  expect(result.bytes).toBeGreaterThan(1000);
  expect(result.width).toBeGreaterThanOrEqual(1000);
  expect(result.height).toBeGreaterThan(result.width * 2);
  await expect(viewer.locator(".pdf-preview-image")).toHaveCount(0);
  expect(controls.publishes).toEqual([]);
  assertClean(controls);
});

test("剪贴板拒绝图片权限时提供原图复制，不误报复制成功", async ({ page }) => {
  await page.addInitScript(() =>
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: {
        write: async () => {
          throw new DOMException("Denied", "NotAllowedError");
        },
      },
    }),
  );
  const controls = await fixture(page);
  const dialog = await preview(page);
  const viewer = dialog.getByRole("region", { name: "发布前 PDF 纪要预览" });
  const copy = viewer.getByRole("button", { name: "复制图片", exact: true });
  await expect(copy).toBeEnabled();
  await copy.click();
  await expect(viewer.locator(".pdf-preview-image")).toBeVisible();
  await expect(
    page.getByText("完整纪要图片已复制，可直接粘贴。", { exact: true }),
  ).toHaveCount(0);
  expect(controls.publishes).toEqual([]);
  assertClean(controls);
});

test("复制图片生成期间刷新预览，不显示旧图片或取消错误", async ({ page }) => {
  await page.addInitScript(() => {
    Object.defineProperty(navigator, "clipboard", {
      value: undefined,
      configurable: true,
    });
    const original = HTMLCanvasElement.prototype.toBlob;
    let held = false;
    HTMLCanvasElement.prototype.toBlob = function (callback, type, quality) {
      if (!held) {
        held = true;
        (window as any).__finishOldImage = () =>
          original.call(this, callback, type, quality);
      } else original.call(this, callback, type, quality);
    };
  });
  const controls = await fixture(page);
  const dialog = await preview(page);
  const viewer = dialog.getByRole("region", { name: "发布前 PDF 纪要预览" });
  const copy = viewer.getByRole("button", { name: "复制图片", exact: true });
  await expect(copy).toBeEnabled();
  await copy.click();
  await expect
    .poll(() => page.evaluate(() => typeof (window as any).__finishOldImage))
    .toBe("function");
  await dialog
    .getByRole("button", { name: "刷新 PDF 预览", exact: true })
    .click();
  await expect(copy).toBeEnabled();
  await page.evaluate(() => (window as any).__finishOldImage());
  await expect(viewer.locator(".pdf-preview-image")).toHaveCount(0);
  await expect(page.getByText("纪要预览已更新", { exact: true })).toHaveCount(
    0,
  );
  await copy.click();
  await expect(viewer.locator(".pdf-preview-image")).toBeVisible();
  expect(controls.publishes).toEqual([]);
  assertClean(controls);
});

for (const mobile of [false, true]) {
  test(`多页PDF滑到底后继续滚动到通知设置，向上可返回预览（${mobile ? "手机" : "笔记本"}）`, async ({
    page,
  }, testInfo) => {
    await page.setViewportSize(
      mobile ? { width: 390, height: 844 } : { width: 1366, height: 768 },
    );
    const controls = await fixture(page);
    controls.previewBody = readFileSync(
      fileURLToPath(new URL("./fixtures/preview-minutes.pdf", import.meta.url)),
    );
    const dialog = await preview(page);
    const viewer = dialog.getByRole("region", { name: "发布前 PDF 纪要预览" });
    await expect(viewer).toBeVisible();
    await expect(viewer.getByRole("status")).toHaveCount(0);
    await expect
      .poll(() => viewer.locator(".pdf-preview-page").count())
      .toBeGreaterThan(1);
    await expect(viewer.locator("canvas").first()).toBeVisible();
    const hasInk = await viewer
      .locator("canvas")
      .first()
      .evaluate((canvas: HTMLCanvasElement) => {
        const pixels = canvas
          .getContext("2d")!
          .getImageData(0, 0, canvas.width, canvas.height).data;
        return pixels.some((value, index) => index % 4 !== 3 && value < 200);
      });
    expect(hasInk).toBe(true);
    await dialog.screenshot({ path: testInfo.outputPath("preview-top.png") });
    const body = dialog.locator(".el-dialog__body");
    const before = await body.evaluate((element) => {
      const pdf = element.querySelector(".pdf-preview")!;
      element.scrollTop +=
        pdf.getBoundingClientRect().bottom -
        element.getBoundingClientRect().bottom +
        10;
      return element.scrollTop;
    });
    const bounds = (await body.boundingBox())!;
    await page.mouse.move(
      bounds.x + bounds.width / 2,
      bounds.y + bounds.height - 100,
    );
    await page.mouse.wheel(0, 500);
    await expect
      .poll(() => body.evaluate((element) => element.scrollTop))
      .toBeGreaterThan(before + 100);
    await expect(
      dialog.getByRole("combobox", { name: "通知接收人", exact: true }),
    ).toBeInViewport();
    await page.mouse.wheel(0, 2000);
    await expect(
      dialog.getByRole("radio", { name: "不通知", exact: true }),
    ).toBeInViewport();
    await expect(
      dialog.getByRole("button", { name: "确认发布", exact: true }),
    ).toBeInViewport();
    await dialog.screenshot({
      path: testInfo.outputPath("preview-settings.png"),
    });
    expect(
      await viewer.evaluate(
        (element) => element.scrollHeight <= element.clientHeight + 1,
      ),
    ).toBe(true);
    await page.mouse.move(bounds.x + bounds.width / 2, bounds.y + 60);
    await page.mouse.wheel(0, -100000);
    await expect(
      viewer.getByRole("link", { name: "打开 PDF", exact: true }),
    ).toBeInViewport();
    await expect(viewer.locator("canvas").first()).toBeVisible();
    const downloadEvent = page.waitForEvent("download");
    await viewer.getByRole("link", { name: "下载 PDF", exact: true }).click();
    expect((await downloadEvent).suggestedFilename()).toMatch(/\.pdf$/);
    await dialog.getByRole("button", { name: "返回编辑", exact: true }).click();
    await preview(page);
    await expect(page.locator(".pdf-preview canvas").first()).toBeVisible();
    expect(controls.publishes).toEqual([]);
    assertClean(controls);
  });
}

test("PDF渲染失败保留打开下载入口，刷新预览后可恢复", async ({ page }) => {
  const controls = await fixture(page);
  controls.previewBody = Buffer.from("%PDF-1.4\n%%EOF");
  const dialog = await preview(page);
  const viewer = dialog.getByRole("region", { name: "发布前 PDF 纪要预览" });
  await expect(viewer.getByRole("alert")).toBeVisible();
  await expect(
    viewer.getByRole("button", { name: "复制图片", exact: true }),
  ).toBeDisabled();
  await expect(
    viewer.getByRole("link", { name: "打开 PDF", exact: true }),
  ).toBeVisible();
  controls.previewBody = previewPdf();
  await dialog
    .getByRole("button", { name: "刷新 PDF 预览", exact: true })
    .click();
  await expect(viewer.getByRole("alert")).toHaveCount(0);
  await expect(viewer.locator("canvas").first()).toBeVisible();
  expect(controls.publishes).toEqual([]);
  assertClean(controls);
});

test("预览网关返回空错误时显示状态码并保留草稿，不出现JSON解析错误", async ({
  page,
}) => {
  const controls = await fixture(page);
  controls.previewFailure = { status: 502, body: "" };
  await page.goto(`/meetings/${firstId}`);
  await page.getByRole("button", { name: "预览并发布", exact: true }).click();
  await expect(
    page.getByText("预览生成失败（HTTP 502），请稍后重试", { exact: true }),
  ).toBeVisible();
  expect(controls.publishes).toEqual([]);
  await expect(page.getByText(/Unexpected end of JSON/)).toHaveCount(0);
  assertClean(controls);
});

test("预览返回HTML错误和空PDF时可重新生成，原有业务校验提示保留", async ({
  page,
}) => {
  const controls = await fixture(page);
  controls.previewFailure = {
    status: 500,
    body: "<html>proxy error</html>",
    contentType: "text/html",
  };
  await page.goto(`/meetings/${firstId}`);
  await page.getByRole("button", { name: "预览并发布", exact: true }).click();
  await expect(
    page.getByText("预览生成失败（HTTP 500），请稍后重试", { exact: true }),
  ).toBeVisible();
  controls.previewFailure = {
    status: 200,
    body: "",
    contentType: "application/pdf",
  };
  await page
    .getByRole("button", { name: "刷新 PDF 预览", exact: true })
    .click();
  await expect(
    page.getByText("预览服务返回空文件，请重新生成预览", { exact: true }),
  ).toBeVisible();
  controls.previewFailure = {
    status: 422,
    body: JSON.stringify({ detail: "请补全责任人" }),
    contentType: "application/json",
  };
  await page
    .getByRole("button", { name: "刷新 PDF 预览", exact: true })
    .click();
  await expect(page.getByText("请补全责任人", { exact: true })).toBeVisible();
  controls.previewFailure = null;
  await page
    .getByRole("button", { name: "刷新 PDF 预览", exact: true })
    .click();
  expect(controls.publishes).toEqual([]);
  assertClean(controls);
});

test("发布已成功但响应为空时读取状态确认成功，且只发布一次", async ({
  page,
}) => {
  const controls = await fixture(page);
  controls.emptyPublishResponse = true;
  const dialog = await preview(page);
  await dialog.getByRole("button", { name: "确认发布", exact: true }).click();
  await expect(
    page.getByRole("dialog", { name: nativeDialogTitle }),
  ).toHaveCount(0);
  expect(controls.plans).toEqual([]);
  expect(controls.publishes).toEqual([firstId]);
  await expect(page.getByText(/Unexpected end of JSON/)).toHaveCount(0);
  assertClean(controls);
});

test("空发布响应且未能核实结果时保留预览，不自动重复提交", async ({ page }) => {
  const controls = await fixture(page);
  controls.emptyPublishResponse = true;
  controls.skipPublishCommit = true;
  const dialog = await preview(page);
  await dialog.getByRole("button", { name: "确认发布", exact: true }).click();
  await expect(
    page.getByText(/发布响应异常，暂未确认本次是否发布成功/),
  ).toBeVisible();
  await expect(dialog).toBeVisible();
  expect(controls.publishes).toEqual([firstId]);
  assertClean(controls);
});

for (const width of [1366, 390]) {
  test(`不通知隐藏名单并直接发布，重新启用通知保留名单 ${width}`, async ({
    page,
  }, testInfo) => {
    await page.setViewportSize({ width, height: 900 });
    const controls = await fixture(page);
    const dialog = await preview(page);
    await dialog.getByRole("button", { name: /采用建议名单/ }).click();
    const recipients = dialog.getByRole("combobox", {
      name: "通知接收人",
      exact: true,
    });
    const original = await dialog
      .locator(".people-select-field")
      .first()
      .textContent();
    await dialog.locator(".el-radio").filter({ hasText: "不通知" }).click();
    await expect(recipients).toHaveCount(0);
    await expect(
      dialog.getByRole("combobox", { name: "邮件抄送人或邮箱", exact: true }),
    ).toHaveCount(0);
    await dialog.screenshot({
      path: testInfo.outputPath(`no-notification-${width}.png`),
    });
    await dialog.locator(".el-radio").filter({ hasText: "发送通知" }).click();
    await expect(recipients).toBeVisible();
    expect(
      await dialog.locator(".people-select-field").first().textContent(),
    ).toBe(original);
    await dialog.locator(".el-radio").filter({ hasText: "不通知" }).click();
    await publish(page);
    await expect(
      page.getByRole("dialog", { name: "确认仅发布、不发送通知", exact: true }),
    ).toHaveCount(0);
    expect(controls.publishes).toEqual([firstId]);
    expect(controls.notificationChoices).toEqual([false]);
    assertClean(controls);
  });
}

test("明确选择仅发布后，下一场接续会议恢复发送通知，当前预览仍保留本人选择", async ({
  page,
}) => {
  const controls = await fixture(page);
  let dialog = await preview(page);
  await dialog.getByRole("button", { name: /采用建议名单/ }).click();
  await dialog.locator(".el-radio").filter({ hasText: "不通知" }).click();
  await dialog.getByRole("button", { name: "返回编辑", exact: true }).click();
  dialog = await preview(page);
  await expect(
    dialog.getByRole("radio", {
      name: "发送通知",
      exact: true,
    }),
  ).not.toBeChecked();
  await dialog.getByRole("button", { name: "确认发布", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "持续新建", exact: true }),
  ).toBeVisible();
  expect(controls.notificationChoices).toEqual([false]);
  await continueMeeting(page);
  dialog = await preview(page);
  await expect(
    dialog.getByRole("radio", {
      name: "发送通知",
      exact: true,
    }),
  ).toBeChecked();
  await publish(page);
  expect(controls.publishes).toEqual([firstId, secondId]);
  expect(controls.notificationChoices).toEqual([false, true]);
  assertClean(controls);
});

test("同一会议创建修订版本后恢复发送通知，不继承上次仅发布选择", async ({
  page,
}) => {
  const controls = await fixture(page);
  let dialog = await preview(page);
  await dialog.locator(".el-radio").filter({ hasText: "不通知" }).click();
  await dialog.getByRole("button", { name: "确认发布", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "持续新建", exact: true }),
  ).toBeVisible();
  expect(controls.notificationChoices).toEqual([false]);
  await page.getByRole("button", { name: "创建修订版本", exact: true }).click();
  const revision = page.getByRole("dialog", {
    name: "创建修订版本",
    exact: true,
  });
  await revision.getByRole("textbox").fill("补充工作1和14的备注");
  await revision.getByRole("button", { name: "确定", exact: true }).click();
  dialog = await preview(page);
  await expect(
    dialog.getByRole("radio", {
      name: "发送通知",
      exact: true,
    }),
  ).toBeChecked();
  await dialog.getByRole("button", { name: /采用建议名单/ }).click();
  await publish(page);
  expect(controls.notificationChoices).toEqual([false, true]);
  expect(controls.publicationReasons).toEqual(["发布会议纪要", "更新会议纪要"]);
  assertClean(controls);
});

test("首场与接续会议发布后仅成功提示，手动打开各自待办", async ({ page }) => {
  const controls = await fixture(page);
  await preview(page);
  await publish(page);
  await page.getByRole("button", { name: "派发企微待办", exact: true }).click();
  let native = page.getByRole("dialog", { name: nativeDialogTitle });
  await expect(native).toContainText("第一场任务");
  await native
    .getByRole("button", { name: "完成，返回会议", exact: true })
    .click();
  await continueMeeting(page);
  await addSecondTask(page);
  await preview(page);
  await publish(page);
  await page.getByRole("button", { name: "派发企微待办", exact: true }).click();
  native = page.getByRole("dialog", { name: nativeDialogTitle });
  await expect(native).toBeVisible();
  await expect(native).toContainText("第二场新增任务");
  await expect(native).not.toContainText("第一场任务");
  await expect(
    page.getByRole("dialog", { name: publicationTitle }),
  ).toHaveCount(0);
  await expect
    .poll(() =>
      native.evaluate((element) => {
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
  await page.screenshot({
    path: "../../.validation/repeat-meeting-native-20261027/second-meeting-native-dialog.png",
    fullPage: false,
  });
  expect(controls.plans).toEqual([firstId, secondId]);
  assertClean(controls);
});

test("接续仅回顾发布后可手动查看待办且不重复创建", async ({ page }) => {
  const controls = await fixture(page);
  await preview(page);
  await publish(page);
  await page.getByRole("button", { name: "派发企微待办", exact: true }).click();
  await page
    .getByRole("dialog", { name: nativeDialogTitle })
    .getByRole("button", { name: "完成，返回会议", exact: true })
    .click();
  await continueMeeting(page);
  await preview(page);
  await publish(page);
  await page.getByRole("button", { name: "派发企微待办", exact: true }).click();
  const native = page.getByRole("dialog", { name: nativeDialogTitle });
  await expect(native).toBeVisible();
  await expect(native).toContainText("本次会议没有需要派发的任务");
  await expect(
    native.getByRole("button", { name: "确认派发全部待办", exact: true }),
  ).toBeDisabled();
  expect(controls.plans).toEqual([firstId, secondId]);
  assertClean(controls);
});

test("发布成功后详情刷新失败仍可手动打开该会议待办窗口，且不会重复发布", async ({
  page,
}) => {
  const controls = await fixture(page);
  controls.failPublishedDetailOnce = firstId;
  const dialog = await preview(page);
  await dialog.getByRole("button", { name: "确认发布", exact: true }).click();
  await page.getByRole("button", { name: "派发企微待办", exact: true }).click();
  const native = page.getByRole("dialog", { name: nativeDialogTitle });
  await expect(native).toBeVisible();
  await expect(native).toContainText("第一场任务");
  expect(controls.failedDetailReads).toEqual([firstId]);
  expect(controls.plans).toEqual([firstId]);
  expect(controls.publishes).toEqual([firstId]);
  assertClean(controls);
});

test("历史会议固定不通知，隐藏不适用的名单", async ({ page }) => {
  const controls = await fixture(page, { historical: true });
  const dialog = await preview(page);
  await expect(
    dialog.getByRole("radio", { name: "不通知", exact: true }),
  ).toBeChecked();
  await expect(
    dialog.getByRole("radio", { name: "发送通知", exact: true }),
  ).toBeDisabled();
  await expect(
    dialog.getByRole("combobox", { name: "通知接收人", exact: true }),
  ).toHaveCount(0);
  await publish(page);
  expect(controls.notificationChoices).toEqual([false]);
  assertClean(controls);
});

for (const width of [1366, 390]) {
  test(`preview recipient drag order persists without sending notifications at ${width}px`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 900 });
    const controls = await fixture(page);
    let dialog = await preview(page);
    const field = () =>
      dialog.locator(".el-form-item").filter({ hasText: "通知接收人" }).first();
    await expect(field().locator(".people-select-name")).toHaveText([
      "隔离主持人",
      "隔离责任人",
    ]);
    const grip = field().getByRole("button", {
      name: "拖动隔离责任人调整顺序",
    });
    await grip.hover();
    const start = await grip.boundingBox();
    const end = await field()
      .locator(".people-sortable-tag")
      .first()
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
    await page.mouse.move(end!.x + 3, end!.y + end!.height / 2, { steps: 15 });
    await page.waitForTimeout(200);
    await page.mouse.move(end!.x + 4, end!.y + end!.height / 2);
    await page.waitForTimeout(200);
    await page.mouse.up();
    await expect(field().locator(".people-select-name")).toHaveText([
      "隔离责任人",
      "隔离主持人",
    ]);
    await expect
      .poll(() => controls.draft().recipient_ids)
      .toEqual(["isolated-state-owner", "isolated-state-host"]);
    await field().screenshot({
      path: `../../.validation/preview-recipients-autosave-20261112/recipients-${width}.png`,
    });
    await dialog.getByRole("button", { name: "返回编辑", exact: true }).click();
    await page.reload();
    dialog = await preview(page);
    await expect(field().locator(".people-select-name")).toHaveText([
      "隔离责任人",
      "隔离主持人",
    ]);
    expect(controls.publishes).toEqual([]);
    assertClean(controls);
  });
}

async function editTitle(page: Page) {
  await page
    .locator(".meeting-info-card")
    .getByRole("button", { name: "编辑", exact: true })
    .click();
  return page.getByRole("textbox", { name: "会议名称", exact: true });
}

test("minute autosave saves continuous edits that keep postponing the debounce", async ({
  page,
}) => {
  await page.clock.install();
  const controls = await fixture(page);
  await expect(page.locator(".el-loading-mask")).toHaveCount(0);
  await page.clock.pauseAt(
    new Date((await page.evaluate(() => Date.now())) + 1000),
  );
  const title = await editTitle(page);
  for (let second = 0; second < 60; second++) {
    await title.fill(`连续编辑 ${second}`);
    await page.clock.runFor(1000);
  }
  await expect.poll(() => controls.saves.length).toBe(1);
  expect(controls.draft().title).toContain("连续编辑");
  await page.clock.runFor(3000);
  await expect.poll(() => controls.draft().title).toBe("连续编辑 59");
  expect(controls.publishes).toEqual([]);
  assertClean(controls);
});

test("clean drafts make no periodic writes", async ({ page }) => {
  await page.clock.install();
  const controls = await fixture(page);
  await page.clock.fastForward(180_000);
  expect(controls.saves).toEqual([]);
  expect(controls.creates).toBe(0);
  assertClean(controls);
});

test("minute autosave skips blank new meetings and creates valid drafts once", async ({
  page,
}) => {
  await page.clock.install();
  const controls = await fixture(page, { startNew: true });
  await expect(page.locator(".el-loading-mask")).toHaveCount(0);
  await page.clock.pauseAt(
    new Date((await page.evaluate(() => Date.now())) + 1000),
  );
  const title = await editTitle(page);
  await title.fill("");
  await page.clock.runFor(60_000);
  expect(controls.creates).toBe(0);
  await title.fill("自动保存新会议");
  await page.clock.runFor(60_000);
  await expect.poll(() => controls.creates).toBe(1);
  await expect(page).toHaveURL(new RegExp(`/meetings/${firstId}$`));
  await page.clock.runFor(120_000);
  expect(controls.creates).toBe(1);
  expect(controls.saves).toEqual([]);
  expect(controls.draft().title).toBe("自动保存新会议");
  assertClean(controls);
});

test("failed autosave keeps local edits and does not retry every minute", async ({
  page,
}) => {
  await page.clock.install();
  const controls = await fixture(page);
  await expect(page.locator(".el-loading-mask")).toHaveCount(0);
  await page.clock.pauseAt(
    new Date((await page.evaluate(() => Date.now())) + 1000),
  );
  controls.saveFailure = true;
  const title = await editTitle(page);
  await title.fill("保存失败仍保留输入");
  await page.clock.runFor(2000);
  await expect(
    page.getByRole("alert").filter({ hasText: "草稿未保存" }),
  ).toBeVisible();
  await page.clock.runFor(180_000);
  expect(controls.saves).toHaveLength(1);
  await expect(title).toHaveValue("保存失败仍保留输入");
  expect(controls.draft().title).toBe("第一场隔离会议");
  expect(controls.publishes).toEqual([]);
  assertClean(controls);
});

for (const width of [1366, 390]) {
  test(`manual save confirms an already saved draft without another write at ${width}px`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 900 });
    const controls = await fixture(page);
    await page.getByRole("button", { name: "保存草稿", exact: true }).click();
    await expect(page.locator(".el-message--success")).toHaveText("草稿已保存");
    expect(controls.saves).toEqual([]);
    expect(controls.creates).toBe(0);
    await expect(page.locator(".el-message--success")).not.toHaveClass(
      /enter-active/,
    );
    await page.screenshot({
      path: `../../.validation/manual-save-feedback-20261112/saved-${width}.png`,
    });
    assertClean(controls);
  });
}

test("manual save reports successful changed content exactly once", async ({
  page,
}) => {
  const controls = await fixture(page);
  const title = await editTitle(page);
  await title.fill("手动保存确认");
  await page.getByRole("button", { name: "保存草稿", exact: true }).click();
  await expect(page.locator(".el-message--success")).toHaveText("草稿已保存");
  expect(controls.draft().title).toBe("手动保存确认");
  expect(controls.saves).toHaveLength(1);
  expect(controls.publishes).toEqual([]);
  assertClean(controls);
});

test("manual save failure shows its reason and preserves local edits for retry", async ({
  page,
}) => {
  const controls = await fixture(page);
  const title = await editTitle(page);
  controls.saveFailure = true;
  await title.fill("失败后待重试的内容");
  await page.getByRole("button", { name: "保存草稿", exact: true }).click();
  await expect(page.locator(".el-message--error")).toContainText(
    "草稿保存失败：模拟保存失败",
  );
  await expect(page.locator(".el-message--success")).toHaveCount(0);
  await expect(title).toHaveValue("失败后待重试的内容");
  expect(controls.draft().title).toBe("第一场隔离会议");
  controls.saveFailure = false;
  await page.getByRole("button", { name: "保存草稿", exact: true }).click();
  await expect(page.locator(".el-message--success")).toHaveText("草稿已保存");
  expect(controls.draft().title).toBe("失败后待重试的内容");
  assertClean(controls);
});

test("automatic save stays silent and a subsequent manual save confirms success", async ({
  page,
}) => {
  const controls = await fixture(page);
  const title = await editTitle(page);
  await title.fill("自动保存无需弹窗");
  await expect.poll(() => controls.draft().title).toBe("自动保存无需弹窗");
  await expect(page.locator(".el-message")).toHaveCount(0);
  await page.getByRole("button", { name: "保存草稿", exact: true }).click();
  await expect(page.locator(".el-message--success")).toHaveText("草稿已保存");
  expect(controls.saves).toHaveLength(1);
  assertClean(controls);
});
