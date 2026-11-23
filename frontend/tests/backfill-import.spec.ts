import { expect, test } from "@playwright/test";
import path from "node:path";
import { fileURLToPath } from "node:url";

const evidence = fileURLToPath(
  new URL("../../../.validation/backfill-import-20261111", import.meta.url),
);

for (const [width, suffix] of [
  [1366, "xlsx"],
  [390, "png"],
  [1366, "pdf"],
] as const) {
  test(`历史导入可统一为本次安排并关联11月2日 ${width} ${suffix}`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 900 });
    const me = {
      id: "admin",
      name: "系统管理员",
      is_admin: true,
      active: true,
      department_id: "dep",
    };
    const users = [
      me,
      { id: "owner", name: "测试责任人", active: true, department_id: "dep" },
    ];
    const directory = {
      users,
      departments: [{ id: "dep", name: "总经办" }],
      series: [
        {
          id: "s",
          name: "管理层周例会",
          department_id: "dep",
          kind: "department",
        },
      ],
    };
    const source = {
      id: "source",
      filename: `1026任务单.${suffix}`,
      diagnostics: [],
      status: "ready",
      error: null,
      candidates: [
        {
          sheet: "会议纪要",
          usable: false,
          review_count: 2,
          new_count: 0,
          issues: [],
          payload: {
            title: "10月26日历史会议",
            meeting_date: "2026-10-26",
            department_id: "dep",
            kind: "department",
            host_id: "admin",
            recorder_id: "admin",
          },
        },
      ],
    };
    let draft: any,
      version = 1,
      recognitionFails = false;
    const mutations: string[] = [],
      errors: string[] = [],
      unexpected: string[] = [];
    const record = () => ({
      id: "history",
      draft,
      draft_version: version,
      published_version: 0,
      has_draft: true,
      historical: true,
      can_edit: true,
      can_delete: true,
      versions: [],
      attachments: [],
      context: {},
    });
    await page.addInitScript(() =>
      sessionStorage.setItem("token", "isolated-backfill-import"),
    );
    page.on("pageerror", (e) => errors.push(e.message));
    await page.route("**/api/**", async (route) => {
      const request = route.request(),
        url = new URL(request.url()),
        endpoint = url.pathname;
      let response: any;
      if (request.method() !== "GET")
        mutations.push(`${request.method()} ${endpoint}`);
      if (endpoint === "/api/me") response = me;
      else if (["/api/directory", "/api/meeting-people"].includes(endpoint))
        response = directory;
      else if (endpoint === "/api/auth/options") response = { wecom: false };
      else if (endpoint === "/api/meetings") response = { items: [], total: 0 };
      else if (endpoint === "/api/meeting-filters") response = {};
      else if (endpoint === "/api/series/s/task-sheet-imports") {
        if (recognitionFails)
          return route.fulfill({
            status: 422,
            json: { detail: "新文件识别失败，请重选文件" },
          });
        response = source;
      } else if (endpoint === "/api/meeting-sheet-imports/source/draft") {
        const headers = request.postDataJSON();
        expect(headers).toMatchObject({
          history_backfill: true,
          backfill_all_new: true,
          meeting_date: "2026-10-26",
        });
        draft = {
          ...headers,
          series_id: "s",
          sheet_import_id: "source",
          import_confirmed_issues: [],
          attendee_ids: [],
          recipient_ids: [],
          cc_ids: [],
          cc_emails: [],
          attachment_ids: [],
          decisions: "",
          absentees: "",
          meeting_time: "",
          location: "",
          items: [1, 2].map((i) => ({
            id: `old-${i}`,
            section: "new",
            content: `历史工作${i}`,
            owner_ids: ["owner"],
            due_date: "2026-11-30",
            status: "未到期",
            task_id: null,
            verifier_id: "admin",
            notes: `原始备注${i}`,
            category: "",
            requirements: "",
            deliverable: "报告",
            source: {
              sheet_import_id: "source",
              sheet_import_item_id: `old-${i}`,
              row: i + 10,
              original_section: "unknown",
            },
          })),
        };
        response = { id: "history", duplicate: false };
      } else if (endpoint === "/api/meetings/history") {
        if (request.method() === "PUT") {
          draft = request.postDataJSON().draft;
          version++;
        }
        response = record();
      } else if (endpoint === "/api/meeting-sheet-imports/source")
        response = { ...source, selected_index: 0 };
      else if (endpoint === "/api/backfill-task-options") {
        expect(url.searchParams.get("meeting_date")).toBe("2026-10-26");
        response = {
          items: [
            {
              meeting_id: "nov02",
              version: 5,
              item_id: "published-row",
              task_id: "existing-task",
              meeting_date: "2026-11-02",
              meeting_title: "11月2日已发布会议",
              content: "历史工作1",
              owner_names: ["测试责任人"],
              due_date: "2026-11-30",
              section: "new",
              status: "未到期",
            },
          ],
        };
      } else {
        unexpected.push(`${request.method()} ${endpoint}`);
        return route.fulfill({
          status: 500,
          json: { detail: "Unexpected test request" },
        });
      }
      await route.fulfill({ json: response });
    });
    await page.goto("/meetings?series=series:s");
    await page.getByRole("button", { name: "导入任务单", exact: true }).click();
    const dialog = page.getByRole("dialog", {
      name: "导入任务单",
      exact: true,
    });
    await dialog.locator("input[type=file]").setInputFiles({
      name: source.filename,
      mimeType: "application/octet-stream",
      buffer: Buffer.from("isolated recognized file"),
    });
    await expect(
      dialog.getByRole("button", { name: "生成会议草稿" }),
    ).toBeDisabled();
    await dialog.getByText("补录历史会议", { exact: true }).click();
    await dialog.getByText("全部作为本次新安排", { exact: true }).click();
    await expect(dialog).toContainText("上期任务 0 条 · 本次任务 2 条");
    await expect(
      dialog.getByRole("button", { name: "生成会议草稿" }),
    ).toBeEnabled();
    await dialog.getByText("补录历史会议", { exact: true }).click();
    await expect(
      dialog.getByText("全部作为本次新安排", { exact: true }),
    ).toHaveCount(0);
    await expect(
      dialog.getByRole("button", { name: "生成会议草稿" }),
    ).toBeDisabled();
    await dialog.getByText("补录历史会议", { exact: true }).click();
    await dialog.getByText("全部作为本次新安排", { exact: true }).click();
    await page.screenshot({
      path: path.join(evidence, `import-${width}-${suffix}.png`),
      animations: "disabled",
    });
    await dialog.getByRole("button", { name: "生成会议草稿" }).click();
    await expect(page).toHaveURL(/\/meetings\/history/);
    const row = page.locator("#work-row-old-1");
    await expect(
      row.getByRole("textbox", { name: "输出物", exact: true }),
    ).toHaveValue("报告");
    await row
      .getByRole("button", { name: "关联已发布任务", exact: true })
      .click();
    const chooser = page.getByRole("dialog", { name: "关联后续已发布任务" });
    await expect(chooser).toContainText("11月2日已发布会议");
    await chooser
      .getByRole("button", { name: "确认关联", exact: true })
      .click();
    await expect
      .poll(() => draft.items[0].backfill_reference?.meeting_id)
      .toBe("nov02");
    await expect(
      row.getByRole("textbox", { name: "工作内容及要求", exact: true }),
    ).toHaveValue("历史工作1");
    expect(draft.items[0].notes).toBe("原始备注1");
    await page.reload();
    await expect(
      page.getByRole("link", { name: /已关联：2026-11-02/ }),
    ).toBeVisible();
    await expect(page.locator("body")).toHaveJSProperty("scrollWidth", width);
    await page.goto("/meetings?series=series:s");
    await page.getByRole("button", { name: "导入任务单", exact: true }).click();
    await dialog
      .locator("input[type=file]")
      .setInputFiles({
        name: source.filename,
        mimeType: "application/octet-stream",
        buffer: Buffer.from("recognized again"),
      });
    await dialog.getByText("补录历史会议", { exact: true }).click();
    await dialog.getByText("全部作为本次新安排", { exact: true }).click();
    await expect(
      dialog.getByRole("button", { name: "生成会议草稿" }),
    ).toBeEnabled();
    recognitionFails = true;
    await dialog
      .locator("input[type=file]")
      .setInputFiles({
        name: source.filename,
        mimeType: "application/octet-stream",
        buffer: Buffer.from("failed replacement file"),
      });
    await expect(dialog.getByRole("alert")).toContainText("新文件识别失败");
    await expect(
      dialog.getByRole("button", { name: "生成会议草稿" }),
    ).toBeDisabled();
    expect(
      mutations.every(
        (x) =>
          x === "POST /api/series/s/task-sheet-imports" ||
          x === "POST /api/meeting-sheet-imports/source/draft" ||
          x === "PUT /api/meetings/history",
      ),
    ).toBe(true);
    expect(errors).toEqual([]);
    expect(unexpected).toEqual([]);
  });
}
