import {
  expect,
  test,
  type BrowserContext,
  type Page,
  type Route,
} from "@playwright/test";
import path from "node:path";
import { fileURLToPath } from "node:url";

const evidence = fileURLToPath(
  new URL(
    "../../../.validation/meeting-history-notify-live-20261028",
    import.meta.url,
  ),
);
const series = "series:information-series";
const meeting = (
  id: string,
  department_id: string | null,
  title: string,
  can_delete = true,
) => ({
  id,
  department_id,
  title,
  series_id: "information-series",
  meeting_date: "2026-10-21",
  kind: department_id ? "department" : "special",
  historical: false,
  department_name:
    department_id === "information"
      ? "信息部"
      : department_id === "finance"
        ? "财务部"
        : "专项会议",
  host_name: "测试主持人",
  item_count: 4,
  rate: 0.75,
  published_version: 1,
  has_draft: false,
  can_delete,
  can_continue: true,
});

async function fixture(context: BrowserContext) {
  const control = {
    rows: [
      meeting("information-old", "information", "信息部上期会议"),
      meeting("finance-old", "finance", "财务部历史会议"),
      meeting("special-old", null, "历史专项会议", false),
    ],
    reads: [] as URL[],
    errors: [] as string[],
    unexpected: [] as string[],
    deletes: [] as string[],
    fail: false,
    hold: false,
    held: [] as { route: Route; payload: unknown }[],
  };
  context.on("page", (page) =>
    page.on("pageerror", (error) => control.errors.push(error.message)),
  );
  await context.addInitScript(() =>
    sessionStorage.setItem("token", "isolated-history-token"),
  );
  await context.route("**/api/**", async (route) => {
    const request = route.request(),
      url = new URL(request.url()),
      endpoint = url.pathname;
    let body: unknown;
    if (endpoint === "/api/me")
      body = {
        id: "host",
        name: "测试主持人",
        is_admin: true,
        active: true,
        department_id: "information",
        can_assign_direct: true,
      };
    else if (endpoint === "/api/auth/options")
      body = { wecom: false, wecom_qr: false };
    else if (endpoint === "/api/directory")
      body = {
        users: [{ id: "host", name: "测试主持人", active: true }],
        departments: [
          { id: "information", name: "信息部", parent_id: null },
          { id: "finance", name: "财务部", parent_id: null },
        ],
        series: [{ id: "information-series", name: "信息部周例会" }],
      };
    else if (endpoint === "/api/history-meeting-departments") {
      control.reads.push(url);
      body = {
        items: [
          {
            key: "information",
            id: "information",
            name: "信息部",
            path: "总部 / 信息部",
            meeting_count: control.rows.filter(
              (item) => item.department_id === "information",
            ).length,
          },
          {
            key: "finance",
            id: "finance",
            name: "财务部",
            path: "财务部",
            meeting_count: control.rows.filter(
              (item) => item.department_id === "finance",
            ).length,
          },
          {
            key: "none",
            id: null,
            name: "专项会议",
            path: "专项会议",
            meeting_count: control.rows.filter(
              (item) => item.department_id === null,
            ).length,
          },
        ].filter((item) => item.meeting_count),
        meeting_total: control.rows.length,
      };
    } else if (endpoint === "/api/meetings" && request.method() === "GET") {
      control.reads.push(url);
      if (control.fail) {
        await route.fulfill({
          status: 503,
          json: { detail: "历史会议暂时无法读取" },
        });
        return;
      }
      const department = url.searchParams.get("department_id"),
        search = url.searchParams.get("q") || "";
      const filtered = control.rows.filter(
        (item) =>
          (!department || (item.department_id || "none") === department) &&
          item.title.includes(search),
      );
      const offset = Number(url.searchParams.get("offset") || 0);
      body = {
        items: structuredClone(filtered.slice(offset, offset + 20)),
        total: filtered.length,
      };
      if (control.hold) {
        control.hold = false;
        control.held.push({ route, payload: body });
        return;
      }
    } else if (/^\/api\/meetings\/[^/]+\/deletion-impact$/.test(endpoint)) {
      const id = endpoint.split("/")[3];
      body = {
        id,
        title: control.rows.find((item) => item.id === id)?.title,
        published: true,
        task_count: 0,
        blocking_meeting_count: 0,
        active_native_todos: 0,
        draft_version: 1,
      };
    } else if (
      /^\/api\/meetings\/[^/]+$/.test(endpoint) &&
      request.method() === "DELETE"
    ) {
      const id = endpoint.split("/")[3];
      control.deletes.push(id);
      control.rows = control.rows.filter((item) => item.id !== id);
      await route.fulfill({ status: 204 });
      return;
    } else {
      control.unexpected.push(`${request.method()} ${endpoint}`);
      await route.fulfill({
        status: 404,
        json: { detail: "Unexpected mock endpoint" },
      });
      return;
    }
    await route.fulfill({ json: body });
  });
  return control;
}

async function open(page: Page, department = "") {
  await page.goto(
    `/history-meetings?series=${encodeURIComponent(series)}${department ? `&department=${department}` : ""}`,
  );
  await expect(
    page.getByRole("heading", { name: "历史会议", exact: true }),
  ).toBeVisible();
}

test("历史页按部门筛选，保持会议系列和详情返回位置", async ({
  page,
  context,
}) => {
  const control = await fixture(context);
  await open(page);
  await expect(
    page.getByRole("link", { name: "信息部上期会议", exact: true }),
  ).toBeVisible();
  await expect(
    page
      .locator("aside")
      .getByRole("link", { name: "导入历史会议", exact: true }),
  ).toHaveCount(0);
  await expect(
    page.locator("aside").getByRole("link", { name: "历史会议", exact: true }),
  ).toHaveCount(0);
  await page
    .getByRole("navigation", { name: "历史会议部门" })
    .getByRole("button", { name: /信息部/ })
    .click();
  await expect(
    page.getByRole("link", { name: "财务部历史会议", exact: true }),
  ).toHaveCount(0);
  const link = page.getByRole("link", { name: "信息部上期会议", exact: true });
  const detailUrl = new URL(
    (await link.getAttribute("href"))!,
    "http://127.0.0.1",
  );
  expect(detailUrl.pathname).toBe("/meetings/information-old");
  expect(detailUrl.searchParams.get("history_department")).toBe("information");
  expect(detailUrl.searchParams.get("history_series")).toBe(series);
  await expect(page).toHaveURL(new RegExp(`department=information`));
  expect(control.reads.length).toBeGreaterThanOrEqual(4);
  expect(
    control.reads.every((url) => url.searchParams.get("series_key") === series),
  ).toBe(true);
  expect(
    control.reads
      .filter((url) => url.pathname === "/api/meetings")
      .every((url) => url.searchParams.get("view") === "history"),
  ).toBe(true);
  expect(control.reads.at(-1)?.searchParams.get("department_id")).toBe(
    "information",
  );
  await expect(page.locator(".el-loading-mask")).toHaveCount(0);
  await page.screenshot({
    path: path.join(evidence, "history-desktop.png"),
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(link).toBeVisible();
  await expect(
    page.getByRole("navigation", { name: "历史会议部门" }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: path.join(evidence, "history-mobile.png"),
    fullPage: true,
  });
  expect(control.errors).toEqual([]);
  expect(control.unexpected).toEqual([]);
});

test("初次加载错误可重试，空部门显示空状态，受限记录不显示删除", async ({
  page,
  context,
}) => {
  const control = await fixture(context);
  control.fail = true;
  await open(page);
  await expect(page.getByRole("alert")).toContainText("历史会议暂时无法读取");
  control.fail = false;
  await page.getByRole("button", { name: "重新加载", exact: true }).click();
  await expect(
    page.getByRole("link", { name: "历史专项会议", exact: true }),
  ).toBeVisible();
  await expect(page.getByRole("alert")).toHaveCount(0);
  const special = page.getByRole("row").filter({
    has: page.getByRole("link", { name: "历史专项会议", exact: true }),
  });
  await expect(
    special.getByRole("button", { name: "删除", exact: true }),
  ).toHaveCount(0);
  await page
    .getByRole("textbox", { name: "搜索历史会议", exact: true })
    .fill("不存在的会议");
  await page.getByRole("button", { name: "查询", exact: true }).click();
  await expect(
    page.getByText("暂无符合条件的历史会议", { exact: true }),
  ).toBeVisible();
  expect(control.errors).toEqual([]);
});

test("历史会议删除后立即移除行，后台读取晚到也不会恢复已删记录", async ({
  page,
  context,
}) => {
  const control = await fixture(context);
  await open(page, "information");
  const link = page.getByRole("link", { name: "信息部上期会议", exact: true });
  await expect(link).toBeVisible();
  control.hold = true;
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await expect.poll(() => control.held.length).toBe(1);
  await page.getByRole("button", { name: "删除", exact: true }).click();
  await page.getByRole("button", { name: "确认删除", exact: true }).click();
  await expect(link).toHaveCount(0);
  await expect(
    page.getByText("暂无符合条件的历史会议", { exact: true }),
  ).toBeVisible();
  const held = control.held.splice(0);
  await Promise.all(
    held.map(({ route, payload }) => route.fulfill({ json: payload })),
  );
  await page.evaluate(
    () =>
      new Promise((resolve) =>
        requestAnimationFrame(() => requestAnimationFrame(resolve)),
      ),
  );
  await expect(link).toHaveCount(0);
  await expect(page).toHaveURL(/department=information/);
  expect(control.deletes).toEqual(["information-old"]);
  expect(control.errors).toEqual([]);
});

test("历史页恢复焦点时同步数据并保留部门和搜索条件，后台失败保留内容", async ({
  page,
  context,
}) => {
  const control = await fixture(context);
  await open(page, "information");
  const search = page.getByRole("textbox", {
    name: "搜索历史会议",
    exact: true,
  });
  await search.fill("信息部");
  await page.getByRole("button", { name: "查询", exact: true }).click();
  await expect(
    page.getByRole("link", { name: "信息部上期会议", exact: true }),
  ).toBeVisible();
  control.fail = true;
  const before = control.reads.length;
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await expect.poll(() => control.reads.length).toBeGreaterThan(before);
  await expect(
    page.getByRole("link", { name: "信息部上期会议", exact: true }),
  ).toBeVisible();
  await expect(page.getByRole("alert")).toHaveCount(0);
  control.fail = false;
  control.rows[0].title = "信息部最新修订";
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await expect(
    page.getByRole("link", { name: "信息部最新修订", exact: true }),
  ).toBeVisible();
  await expect(search).toHaveValue("信息部");
  expect(control.reads.at(-1)?.searchParams.get("department_id")).toBe(
    "information",
  );
  expect(control.reads.at(-1)?.searchParams.get("q")).toBe("信息部");
  expect(control.errors).toEqual([]);
});
