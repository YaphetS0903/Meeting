import { expect, test } from "@playwright/test";
import { fileURLToPath } from "node:url";

const evidence = fileURLToPath(
  new URL(
    "../../../.validation/department-draft-delete-20261104/",
    import.meta.url,
  ),
);

for (const width of [1366, 390]) {
  for (const published of [false, true]) {
    test(`部门角色删除权限保存前后保持一致 ${width} ${published ? "已发布含修订草稿" : "未发布草稿"}`, async ({
      page,
    }) => {
      await page.setViewportSize({ width, height: 900 });
      const me = {
        id: "editor",
        name: "会议任务单管理员",
        active: true,
        is_admin: false,
        department_id: "dep",
      };
      const host = {
        id: "host",
        name: "主持人",
        active: true,
        department_id: "dep",
      };
      const users = [me, host];
      const departments = [{ id: "dep", name: "信息部" }];
      let draft: any = {
        title: "部门会议草稿",
        department_id: "dep",
        series_id: "s",
        kind: "department",
        meeting_date: "2026-11-04",
        meeting_time: "09:00",
        location: "会议室",
        host_id: "host",
        recorder_id: "host",
        reviewer_id: null,
        attendee_ids: [],
        attachment_ids: [],
        recipient_ids: [],
        cc_ids: [],
        cc_emails: [],
        absentees: "",
        decisions: "",
        items: [
          {
            id: "task",
            section: "new",
            content: "核对工作事项",
            owner_ids: ["host"],
            due_date: "2026-11-10",
            status: "未到期",
            notes: "",
            deliverable: "报告",
          },
        ],
      };
      let version = 1,
        saves = 0;
      const meeting = () => ({
        id: "m",
        ...draft,
        draft,
        draft_version: version,
        has_draft: true,
        published_version: published ? 1 : 0,
        can_edit: true,
        can_delete: !published,
        can_continue: published,
        historical: false,
        attachments: [],
        context: {},
        versions: published ? [{ number: 1, snapshot: draft }] : [],
      });
      const errors: string[] = [];
      page.on("pageerror", (error) => errors.push(error.message));
      await page.addInitScript(() =>
        sessionStorage.setItem("token", "isolated-department-delete"),
      );
      await page.route("**/api/**", async (route) => {
        const path = new URL(route.request().url()).pathname;
        if (route.request().method() === "PUT" && path === "/api/meetings/m") {
          draft = route.request().postDataJSON().draft;
          version++;
          saves++;
          return route.fulfill({ json: meeting() });
        }
        expect(
          route.request().method(),
          `No production mutation: ${path}`,
        ).toBe("GET");
        let body: any = [];
        if (path === "/api/me") body = me;
        else if (path === "/api/directory")
          body = {
            users,
            departments,
            series: [{ id: "s", name: "部门会议系列", department_id: "dep" }],
          };
        else if (path === "/api/auth/options") body = { wecom: false };
        else if (path === "/api/meeting-people") body = { users, departments };
        else if (path === "/api/meetings/m") body = meeting();
        await route.fulfill({ json: body });
      });
      await page.goto("/meetings/m?series=series:s");
      await expect(
        page.getByText("部门会议草稿", { exact: true }).first(),
      ).toBeVisible();
      const remove = page.getByRole("button", {
        name: "删除会议",
        exact: true,
      });
      if (published) {
        await expect(remove).toHaveCount(0);
        await expect(
          page.getByRole("button", { name: "持续新建", exact: true }),
        ).toBeVisible();
      } else {
        await expect(remove).toBeVisible();
        await page
          .getByRole("textbox", { name: "备注", exact: true })
          .fill("保存后仍可删除部门草稿");
        await page
          .getByRole("button", { name: "保存草稿", exact: true })
          .click();
        await expect.poll(() => saves).toBeGreaterThan(0);
        await expect(remove).toBeVisible();
        await expect(
          page.getByRole("status").filter({ hasText: "已保存" }).first(),
        ).toBeVisible();
      }
      await page.screenshot({
        path:
          evidence +
          `permission-${width}-${published ? "published" : "draft"}.png`,
        fullPage: true,
      });
      expect(errors).toEqual([]);
    });
  }
}
