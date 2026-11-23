import { expect, test } from "@playwright/test";

for (const width of [1366, 390]) {
  test(`PDF和照片后台识别、关闭恢复与重试 ${width}`, async ({ page }, info) => {
    await page.setViewportSize({ width, height: 768 });
    const me = {
      id: "admin",
      name: "系统管理员",
      is_admin: true,
      active: true,
      department_id: "d",
    };
    const departments = [{ id: "d", name: "技术部" }];
    let status = "pending";
    let uploads = 0;
    let retries = 0;
    const writes: string[] = [];
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(e.message));
    const source = () => ({
      id: "photo-source",
      filename: "照片1.png",
      filenames: ["照片1.png", "照片2.jpg"],
      status,
      error: status === "failed" ? "图片模糊，请重新识别" : null,
      diagnostics: [],
      candidates:
        status === "ready"
          ? [
              {
                usable: true,
                sheet: "文档任务单",
                review_count: 2,
                new_count: 1,
                issues: [{ id: "review", message: "请核对文档" }],
                payload: {
                  title: "识别测试会议",
                  department_id: "d",
                  meeting_date: "2026-10-15",
                  kind: "department",
                  host_id: "admin",
                  recorder_id: "admin",
                },
              },
            ]
          : [],
    });
    await page.addInitScript(() =>
      sessionStorage.setItem("token", "isolated-import-test"),
    );
    await page.route("**/api/**", async (route) => {
      const req = route.request(),
        path = new URL(req.url()).pathname;
      let body: any = [];
      if (req.method() !== "GET") {
        writes.push(path);
        if (path.endsWith("/task-sheet-imports")) {
          uploads++;
          expect(req.postDataBuffer()?.toString()).toContain('filename="');
          return route.fulfill({ json: source() });
        }
        if (path.endsWith("/retry")) {
          retries++;
          status = "processing";
          return route.fulfill({ json: source() });
        }
        throw new Error(`Unexpected mutation: ${path}`);
      }
      if (path === "/api/me") body = me;
      else if (path === "/api/directory")
        body = {
          users: [me],
          departments,
          series: [{ id: "s", name: "测试系列", department_id: "d" }],
        };
      else if (path === "/api/auth/options") body = { wecom: false };
      else if (path === "/api/meeting-people")
        body = { users: [me], departments };
      else if (path === "/api/meetings") body = { items: [], total: 0 };
      else if (path === "/api/meeting-filters") body = {};
      else if (path === "/api/meeting-sheet-imports/photo-source") {
        expect(req.headers()["x-operation-context"]).toBe("background");
        body = source();
      }
      await route.fulfill({ json: body });
    });
    await page.goto("/meetings?series=series:s");
    const open = page.getByRole("button", { name: "导入任务单", exact: true });
    await open.click();
    const dialog = page.getByRole("dialog", { name: "导入任务单" });
    const input = dialog.locator("input[type=file]");
    await expect(input).toHaveAttribute(
      "accept",
      ".xls,.xlsx,.pdf,.png,.jpg,.jpeg",
    );
    await input.setInputFiles([
      {
        name: "混选.pdf",
        mimeType: "application/pdf",
        buffer: Buffer.from("pdf"),
      },
      { name: "图片.png", mimeType: "image/png", buffer: Buffer.from("png") },
    ]);
    await expect(dialog.getByRole("alert")).toContainText("一次选择一个");
    expect(uploads).toBe(0);
    await input.setInputFiles([
      {
        name: "照片1.png",
        mimeType: "image/png",
        buffer: Buffer.from("image1"),
      },
      {
        name: "照片2.jpg",
        mimeType: "image/jpeg",
        buffer: Buffer.from("image2"),
      },
    ]);
    await expect(dialog.getByRole("status")).toContainText("正在识别");
    await expect(
      dialog.getByRole("button", { name: "生成会议草稿" }),
    ).toBeDisabled();
    await page.screenshot({
      path: info.outputPath(`pending-${width}.png`),
      fullPage: true,
      animations: "disabled",
    });
    await dialog.getByRole("button", { name: "先关闭" }).click();
    status = "failed";
    await open.click();
    await expect(dialog.getByRole("alert")).toContainText("图片模糊");
    await dialog.getByRole("button", { name: "重新识别", exact: true }).click();
    expect(retries).toBe(1);
    status = "ready";
    await expect(dialog).toContainText("上期任务 2 条", { timeout: 6000 });
    await expect(dialog.getByLabel("导入会议名称")).toHaveValue("识别测试会议");
    await expect(
      dialog.getByRole("button", { name: "生成会议草稿" }),
    ).toBeEnabled();
    await page.screenshot({
      path: info.outputPath(`ready-${width}.png`),
      fullPage: true,
      animations: "disabled",
    });
    const bounds = await dialog.boundingBox();
    expect(bounds!.x).toBeGreaterThanOrEqual(0);
    expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(width);
    expect(bounds!.y).toBeGreaterThanOrEqual(0);
    expect(bounds!.y + bounds!.height).toBeLessThanOrEqual(768);
    await dialog.getByRole("button", { name: "取消", exact: true }).click();
    status = "pending";
    await open.click();
    await expect(dialog.getByRole("status")).toContainText("正在识别");
    await dialog.getByRole("button", { name: "先关闭" }).click();
    status = "ready";
    await page.reload();
    await open.click();
    await expect(dialog).toContainText("上期任务 2 条");
    expect(uploads).toBe(1);
    expect(writes).toEqual([
      "/api/series/s/task-sheet-imports",
      "/api/meeting-sheet-imports/photo-source/retry",
    ]);
    expect(errors).toEqual([]);
  });
}
