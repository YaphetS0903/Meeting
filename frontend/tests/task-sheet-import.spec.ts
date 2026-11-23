import { expect, test } from "@playwright/test";
for (const width of [1366, 1697, 390]) {
  test(`系列内上传识别并创建草稿，保留原日期与核对项（${width}）`, async ({
    page,
  }, testInfo) => {
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
      { id: "host", name: "朱慧铃", active: true, department_id: "dep" },
      { id: "a", name: "责任人甲", active: true, department_id: "dep" },
      { id: "b", name: "责任人乙", active: true, department_id: "dep" },
    ];
    const departments = [{ id: "dep", name: "总经办" }];
    const items = [
      {
        id: "r1",
        section: "review",
        content: "上期已完成任务",
        owner_ids: ["a"],
        due_date: "2026-10-10",
        status: "已完成",
        notes: "",
        source: {
          sheet_import_id: "source",
          sheet_import_item_id: "r1",
          row: 11,
          raw_owners: "责任人甲",
          raw_due_date: "46305",
          raw_status: "已完成",
        },
      },
      {
        id: "r2",
        section: "review",
        content: "关键信息识别",
        owner_ids: ["a"],
        due_date: "2026-10-10",
        status: "未完成",
        notes: "延期到10/20",
        historical_work_id: "sheet:source:r2",
        source: {
          sheet_import_id: "source",
          sheet_import_item_id: "r2",
          row: 12,
          raw_owners: "责任人甲、李肖",
          raw_due_date: "10/10",
          raw_status: "未完成",
        },
      },
      {
        id: "n1",
        section: "new",
        content: "本次新任务",
        owner_ids: ["b"],
        due_date: "2026-10-30",
        status: "未到期",
        notes: "新任务备注",
        deliverable: "报告",
        source: {
          sheet_import_id: "source",
          sheet_import_item_id: "n1",
          row: 20,
        },
      },
      {
        id: "n2",
        section: "new",
        content: "本次正常任务",
        owner_ids: ["b"],
        due_date: "2026-10-31",
        status: "未到期",
        notes: "保留任务备注",
        deliverable: "报告",
        source: {
          sheet_import_id: "source",
          sheet_import_item_id: "n2",
          row: 21,
          raw_owners: "责任人乙",
          raw_due_date: "46326",
          raw_status: "未到期",
          date_history: ["2026-10-20"],
        },
      },
    ];
    const issues = [
      {
        id: "issue",
        code: "date_note_conflict",
        field: "due_date",
        item_id: "r2",
        row: 12,
        message:
          "计划完成日期与备注中的延期日期不一致，请手动核对；系统保留日期栏原值",
        raw: "2026-10-10；备注：延期到10/20",
      },
      {
        id: "owner-issue",
        code: "owner_unmatched",
        field: "owner_ids",
        item_id: "r2",
        row: 12,
        message: "责任人未唯一匹配，请手动选择后确认",
        raw: "李肖",
      },
      {
        id: "continuation-issue",
        code: "continuation_check",
        field: "continuation",
        item_id: "n1",
        review_item_id: "r2",
        row: 20,
        message: "请确认是延续还是独立新任务",
        raw: "本次新任务",
      },
    ];
    const source = {
      id: "source",
      filename: "历史模板.xls",
      candidates: [
        {
          sheet: "会议纪要",
          usable: true,
          review_count: 2,
          new_count: 2,
          issues,
          payload: {
            title: "系统相关优化方案讨论会议纪要",
            department_id: "dep",
            meeting_date: "2026-10-15",
            kind: "special",
            host_id: "host",
            recorder_id: "admin",
          },
        },
      ],
      diagnostics: [],
      selected_index: null,
    };
    let draft: any = {
      ...source.candidates[0].payload,
      series_id: "s",
      sheet_import_id: "source",
      import_confirmed_issues: [],
      items,
      attendee_ids: [],
      reviewer_id: null,
      attachment_ids: [],
      recipient_ids: [],
      cc_ids: [],
      cc_emails: [],
      meeting_time: "10:00",
      location: "办公室",
      absentees: "",
      decisions: "",
    };
    const writes: any[] = [];
    let version = 1,
      created = false,
      failUpload = true;
    const meeting = () => ({
      id: "m",
      ...draft,
      draft,
      draft_version: version,
      published_version: 0,
      has_draft: true,
      historical: false,
      can_edit: true,
      can_delete: true,
      versions: [],
      attachments: [],
      context: {},
    });
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(e.message));
    await page.addInitScript(() =>
      sessionStorage.setItem("token", "isolated-sheet-import"),
    );
    await page.route("**/api/**", async (route) => {
      const path = new URL(route.request().url()).pathname,
        method = route.request().method();
      let body: any = [];
      if (method !== "GET") {
        writes.push({ path, method });
        if (path === "/api/series/s/task-sheet-imports") {
          if (failUpload) {
            failUpload = false;
            return route.fulfill({
              status: 422,
              json: { detail: "未识别到会议任务单，请重新选择文件" },
            });
          }
          return route.fulfill({ json: source });
        }
        if (path === "/api/meeting-sheet-imports/source/draft") {
          expect(route.request().postDataJSON()).toMatchObject({
            candidate_index: 0,
            host_id: "host",
            department_id: "dep",
          });
          created = true;
          return route.fulfill({ json: { id: "m", duplicate: false } });
        }
        if (path === "/api/meetings/m" && method === "PUT") {
          draft = route.request().postDataJSON().draft;
          version++;
          return route.fulfill({ json: meeting() });
        }
        throw new Error(`Unexpected write ${method} ${path}`);
      }
      if (path === "/api/me") body = me;
      else if (path === "/api/directory")
        body = {
          users,
          departments,
          series: [{ id: "s", name: "测试系列", department_id: "dep" }],
        };
      else if (path === "/api/auth/options") body = { wecom: false };
      else if (path === "/api/meeting-people") body = { users, departments };
      else if (path === "/api/meetings") body = { items: [], total: 0 };
      else if (path === "/api/meeting-filters") body = {};
      else if (path === "/api/meetings/m") body = meeting();
      else if (path === "/api/meeting-sheet-imports/source")
        body = { ...source, selected_index: 0 };
      await route.fulfill({ json: body });
    });
    await page.goto("/meetings?series=series:s");
    const trigger = page.getByRole("button", {
      name: "导入任务单",
      exact: true,
    });
    await expect(trigger).toBeVisible();
    await trigger.click();
    const dialog = page.getByRole("dialog", { name: "导入任务单" });
    await dialog.locator("input[type=file]").setInputFiles({
      name: "历史模板.xls",
      mimeType: "application/vnd.ms-excel",
      buffer: Buffer.from("isolated mock file"),
    });
    await expect(dialog.getByRole("alert")).toContainText("未识别到会议任务单");
    await dialog.locator("input[type=file]").setInputFiles({
      name: "历史模板.xls",
      mimeType: "application/vnd.ms-excel",
      buffer: Buffer.from("isolated mock file"),
    });
    await expect(dialog).toContainText("上期任务 2 条");
    await expect(dialog.getByLabel("导入会议名称")).toHaveValue(
      "系统相关优化方案讨论会议纪要",
    );
    await page.screenshot({
      path: testInfo.outputPath(`upload-${width}.png`),
      fullPage: true,
      animations: "disabled",
    });
    await dialog
      .getByRole("button", { name: "生成会议草稿", exact: true })
      .click();
    await expect(page).toHaveURL(/\/meetings\/m\?/);
    expect(created).toBe(true);
    await expect(page.getByRole("region", { name: "导入核对" })).toHaveCount(0);
    const reviewTable = page.locator("section").filter({
      has: page.getByRole("heading", {
        name: "上期会议任务跟踪情况",
        exact: true,
      }),
    });
    const taskRow = reviewTable.locator("tbody > tr").nth(1);
    await expect(
      page.getByText("还有 3 项待核对，请在对应任务行处理。", { exact: false }),
    ).toBeVisible();
    await expect(taskRow.getByLabel("上期计划完成时间")).toHaveValue(
      "2026-10-10",
    );
    await expect(taskRow).toContainText("备注：延期到10/20");
    await expect(taskRow).toContainText("原表第12行");
    await expect(taskRow).toContainText("原文：李肖");
    await expect(taskRow).not.toContainText("原表：");
    const cleanReview = reviewTable.locator("tbody > tr").first();
    await expect(cleanReview.getByLabel("上期工作内容")).toHaveValue(
      "上期已完成任务",
    );
    await expect(cleanReview.getByLabel("上期计划完成时间")).toHaveValue(
      "2026-10-10",
    );
    await expect(cleanReview).not.toContainText("原表");
    await expect(cleanReview).not.toContainText("46305");
    await expect(
      cleanReview.getByRole("group", { name: "本行核对提示" }),
    ).toHaveCount(0);
    await expect(cleanReview.getByRole("checkbox")).toHaveCount(0);
    await expect(taskRow.getByRole("checkbox")).toHaveCount(1);
    await page.getByRole("button", { name: "预览并发布", exact: true }).click();
    await expect(
      page.getByText("请在会议信息或对应任务行核对确认后再预览发布", {
        exact: true,
      }),
    ).toBeVisible();
    await taskRow
      .getByRole("combobox", { name: "上期责任人", exact: true })
      .fill("责任人乙");
    await page.getByRole("option", { name: "责任人乙", exact: true }).click();
    await taskRow.getByText("本行已核对", { exact: true }).click();
    const currentTable = page.locator("section").filter({
      has: page.getByRole("heading", {
        name: "本次会议任务部署",
        exact: true,
      }),
    });
    const currentRow = currentTable.locator("tbody > tr").first();
    const cleanCurrent = currentTable.locator("tbody > tr").nth(1);
    await expect(currentRow).toContainText("原表第20行");
    await expect(cleanCurrent.getByLabel("工作内容及要求")).toHaveValue(
      "本次正常任务",
    );
    await expect(
      cleanCurrent.getByLabel("计划完成时间", { exact: true }),
    ).toHaveValue("2026-10-31");
    await expect(cleanCurrent).toContainText("2026-10-20");
    await expect(cleanCurrent).not.toContainText("原表");
    await expect(cleanCurrent).not.toContainText("46326");
    await expect(cleanCurrent.getByRole("checkbox")).toHaveCount(0);
    await currentRow
      .getByRole("combobox", { name: "核对任务延续关系", exact: true })
      .press("Enter");
    await page
      .getByRole("option", { name: "延续这条上期任务", exact: true })
      .click();
    await currentRow.getByText("本行已核对", { exact: true }).click();
    await expect(
      page.getByText("导入提示已核对，可继续检查下方任务内容。", {
        exact: false,
      }),
    ).toBeVisible();
    await expect(page.getByLabel("上期工作内容").first()).toHaveValue(
      "上期已完成任务",
    );
    await expect.poll(() => draft.import_confirmed_issues).toEqual([
      "issue",
      "owner-issue",
      "continuation-issue",
    ]);
    expect(draft.items[1].due_date).toBe("2026-10-10");
    expect(draft.items[1].owner_ids).toEqual(["a", "b"]);
    expect(draft.items[2].historical_work_id).toBe("sheet:source:r2");
    expect(draft.items[0].source).toMatchObject({
      row: 11,
      raw_due_date: "46305",
      raw_owners: "责任人甲",
      raw_status: "已完成",
    });
    expect(draft.items[3].source).toMatchObject({
      row: 21,
      raw_due_date: "46326",
      date_history: ["2026-10-20"],
    });
    await page.reload();
    await expect(page.getByRole("region", { name: "导入核对" })).toHaveCount(0);
    await expect(
      page.getByText("导入提示已核对，可继续检查下方任务内容。", {
        exact: false,
      }),
    ).toBeVisible();
    await expect(
      page.getByRole("checkbox", { name: "本行已核对" }),
    ).toHaveCount(2);
    for (const checkbox of await page
      .getByRole("checkbox", { name: "本行已核对" })
      .all())
      await expect(checkbox).toBeChecked();
    await page.evaluate(() => {
      (document.activeElement as HTMLElement)?.blur();
      window.scrollTo(0, 0);
    });
    await expect(page.locator(".el-message")).toHaveCount(0);
    await page.screenshot({
      path: testInfo.outputPath(`draft-${width}.png`),
      fullPage: true,
      animations: "disabled",
    });
    expect(writes.some((w) => w.path.includes("/publish"))).toBe(false);
    expect(errors).toEqual([]);
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth + 1,
      ),
    ).toBe(true);
  });
}
