import { expect, test, type Page } from "@playwright/test";
import fs from "node:fs";
import { fileURLToPath } from "node:url";

const evidence = fileURLToPath(
  new URL(
    "../../../.validation/rollback-not-due-carry-20261103/",
    import.meta.url,
  ),
);
const host = {
  id: "host",
  name: "主持甲",
  active: true,
  department_id: "dept",
  email: "host@example.test",
  wecom_id: "host",
};
const owner = { ...host, id: "owner", name: "责任乙", wecom_id: "owner" };
const item = {
  category: "",
  requirements: "",
  deliverable: "",
  notes: "原备注",
  exclusion_reason: "",
  owner_ids: ["owner"],
  verifier_id: "host",
  task_version: 1,
};

async function setup(page: Page, readOnly = false) {
  let draft: any = {
    title: "未到期自动带入回退验收",
    department_id: "dept",
    series_id: null,
    kind: "department",
    host_id: "host",
    recorder_id: "host",
    meeting_date: "2026-11-03",
    attendee_ids: ["host", "owner"],
    attachment_ids: [],
    recipient_ids: [],
    cc_ids: [],
    cc_emails: [],
    items: [
      ...Array.from({ length: 6 }, (_, n) => ({
        ...item,
        id: `review-${n}`,
        section: "review",
        content: `未到期事项${n + 1}`,
        status: "未到期",
        due_date: `2026-11-${String(n + 10).padStart(2, "0")}`,
        task_id: `task-${n}`,
      })),
      ...["未完成", "已完成", "关闭", "不统计"].map((status, n) => ({
        ...item,
        id: `terminal-${n}`,
        section: "review",
        content: `${status}事项`,
        status,
        due_date: "2026-11-01",
        task_id: `other-${n}`,
      })),
      {
        ...item,
        id: "existing-plan",
        section: "new",
        content: "手动新增工作",
        status: "未到期",
        due_date: "2026-11-20",
      },
      {
        ...item,
        id: "already-carried",
        source: { carried_not_due_from_item_id: "review-5" },
        section: "new",
        content: "未到期事项6",
        status: "未到期",
        due_date: "2026-11-15",
        task_id: "task-5",
      },
    ],
  };
  let version = 1;
  const writes: any[] = [],
    previews: any[] = [],
    errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.addInitScript(() =>
    sessionStorage.setItem("token", "isolated-not-due"),
  );
  await page.route("**/api/**", async (route) => {
    const request = route.request(),
      path = new URL(request.url()).pathname;
    let body: any;
    if (path === "/api/me") body = host;
    else if (path === "/api/auth/options") body = { wecom: false };
    else if (path === "/api/directory")
      body = {
        users: [host, owner],
        departments: [{ id: "dept", name: "演示部门" }],
        series: [],
      };
    else if (path === "/api/meeting-people") body = { users: [host, owner] };
    else if (path === "/api/meetings/check-not-due/preview") {
      previews.push({
        ...request.postDataJSON(),
        snapshot: structuredClone(draft),
      });
      return route.fulfill({
        path: fileURLToPath(
          new URL("./fixtures/preview-minutes.pdf", import.meta.url),
        ),
        contentType: "application/pdf",
      });
    } else if (path === "/api/meetings/check-not-due") {
      if (request.method() === "PUT") {
        expect(readOnly).toBe(false);
        expect(request.postDataJSON().expected_version).toBe(version);
        draft = request.postDataJSON().draft;
        writes.push(structuredClone(draft));
        version++;
      }
      body = {
        id: "check-not-due",
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
    } else throw new Error(`Unexpected request: ${request.method()} ${path}`);
    return route.fulfill({ json: body });
  });
  return { writes, previews, errors, draft: () => draft };
}

for (const width of [1366, 390]) {
  test(`rollback keeps not-due review out of current arrangements at ${width}px`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 900 });
    const result = await setup(page);
    await page.goto("/meetings/check-not-due");
    const plans = page.locator(".meeting-plan-table tbody tr");
    await expect(plans).toHaveCount(2);
    const selectResult = async (n: number, status: string) => {
      const input = page.getByRole("combobox", {
        name: `第${n}项完成结果（必填）`,
        exact: true,
      });
      await expect(page.locator(".el-loading-mask")).toHaveCount(0);
      await input.focus();
      await input.press("ArrowDown");
      await input.press("Home");
      for (
        let i = 0;
        i < ["已完成", "未完成", "未到期", "不统计", "关闭"].indexOf(status);
        i++
      )
        await input.press("ArrowDown");
      await input.press("Enter");
    };
    // Editing review results must neither auto-create current tasks nor delete
    // tasks already saved by a user under the previous implementation.
    await selectResult(1, "未完成");
    await selectResult(6, "已完成");
    await expect(plans).toHaveCount(2);
    await page.getByRole("button", { name: "保存草稿", exact: true }).click();
    const originalCurrent = structuredClone(
      result.draft().items.filter((row: any) => row.section === "new"),
    );
    expect(originalCurrent.map((row: any) => row.id)).toEqual([
      "existing-plan",
      "already-carried",
    ]);
    await page.reload();
    await expect(plans).toHaveCount(2);
    await page.getByRole("button", { name: "预览并发布", exact: true }).click();
    await expect.poll(() => result.previews.length).toBe(1);
    const snapshot = result.previews[0].snapshot;
    expect(snapshot.items.filter((row: any) => row.section === "new")).toEqual(
      originalCurrent,
    );
    expect(
      snapshot.items.filter((row: any) => row.section === "review"),
    ).toHaveLength(10);
    expect(
      snapshot.items.find((row: any) => row.id === "review-0").status,
    ).toBe("未完成");
    expect(
      snapshot.items.find((row: any) => row.id === "review-5").status,
    ).toBe("已完成");
    fs.writeFileSync(
      `${evidence}/saved-preview-${width}.json`,
      JSON.stringify(snapshot, null, 2),
    );
    expect(result.errors).toEqual([]);
  });
}

test("viewing a published meeting does not add arrangements or create a draft", async ({
  page,
}) => {
  const result = await setup(page, true);
  await page.goto("/meetings/check-not-due");
  await expect(page.locator(".meeting-plan-table tbody tr")).toHaveCount(2);
  await expect(
    page.getByRole("button", { name: "保存草稿", exact: true }),
  ).toHaveCount(0);
  expect(result.writes).toEqual([]);
  expect(result.previews).toEqual([]);
  expect(result.errors).toEqual([]);
});
