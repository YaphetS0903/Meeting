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
    "../../../.validation/remove-history-import-20261028",
    import.meta.url,
  ),
);
const seriesId = "live-series";
const seriesKey = `series:${seriesId}`;
const row = (id: string, title: string, date: string) => ({
  id,
  title,
  meeting_date: date,
  series_id: seriesId,
  department_id: "information",
  kind: "department",
  historical: false,
  host_name: "测试主持人",
  item_count: 3,
  rate: 1,
  published_version: 1,
  has_draft: false,
  can_delete: true,
  can_continue: true,
  can_request_edit: false,
  pending_edit_count: 0,
});

async function fixture(context: BrowserContext, admin = true) {
  const control = {
    rows: [
      row("latest", "最新会议", "2026-10-28"),
      row("previous", "上次会议", "2026-10-21"),
    ],
    reads: [] as string[],
    next: [] as ("hold" | "fail")[],
    held: [] as { route: Route; payload: unknown }[],
    failDelete: false,
    staleDeleteReads: 0,
    beforeDelete: [] as ReturnType<typeof row>[],
    deletes: [] as string[],
    errors: [] as string[],
    unexpected: [] as string[],
  };
  context.on("page", (page) =>
    page.on("pageerror", (error) => control.errors.push(error.message)),
  );
  await context.addInitScript(() => {
    if (location.origin === "http://127.0.0.1:5202") {
      sessionStorage.setItem("token", "isolated-live-list-token");
    }
  });
  await context.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const pathname = url.pathname;
    let body: unknown;
    if (pathname === "/api/me")
      body = {
        id: "host",
        name: "测试主持人",
        is_admin: admin,
        can_correct_history: true,
        active: true,
        department_id: "information",
        can_assign_direct: true,
      };
    else if (pathname === "/api/auth/options")
      body = { wecom: false, wecom_qr: false };
    else if (pathname === "/api/directory")
      body = {
        users: [{ id: "host", name: "测试主持人", active: true }],
        departments: [{ id: "information", name: "信息部", parent_id: null }],
        series: [{ id: seriesId, name: "信息部周例会" }],
      };
    else if (pathname === "/api/meeting-series") {
      control.reads.push(url.search);
      body = {
        items: [
          {
            key: seriesKey,
            name: "信息部周例会",
            meeting_count: control.rows.length ? 1 : 0,
            latest_date: control.rows[0]?.meeting_date,
            pending_edit_count: 0,
          },
        ],
        total: 1,
        meeting_total: control.rows.length ? 1 : 0,
      };
    } else if (pathname === "/api/meetings" && request.method() === "GET") {
      control.reads.push(url.search);
      const listing =
        control.staleDeleteReads > 0 && control.beforeDelete.length
          ? (control.staleDeleteReads--, control.beforeDelete)
          : control.rows;
      const items =
        url.searchParams.get("view") === "current"
          ? listing.slice(0, 1)
          : listing.slice(1);
      const q = url.searchParams.get("q") || "";
      const filtered = items.filter((item) => item.title.includes(q));
      const offset = Number(url.searchParams.get("offset") || 0);
      body = {
        items: structuredClone(filtered.slice(offset, offset + 20)),
        total: filtered.length,
      };
      const mode = control.next.shift();
      if (mode === "hold") {
        control.held.push({ route, payload: body });
        return;
      }
      if (mode === "fail") {
        await route.fulfill({ status: 503, json: { detail: "刷新暂不可用" } });
        return;
      }
    } else if (/^\/api\/meetings\/[^/]+\/deletion-impact$/.test(pathname)) {
      const id = pathname.split("/")[3];
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
      /^\/api\/meetings\/[^/]+$/.test(pathname) &&
      request.method() === "DELETE"
    ) {
      const id = pathname.split("/")[3];
      if (control.failDelete) {
        await route.fulfill({
          status: 409,
          json: { detail: "会议已被修改，请重试" },
        });
        return;
      }
      control.deletes.push(id);
      control.beforeDelete = structuredClone(control.rows);
      control.rows = control.rows.filter((item) => item.id !== id);
      await route.fulfill({ status: 204 });
      return;
    } else {
      control.unexpected.push(`${request.method()} ${pathname}`);
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

async function open(page: Page) {
  await page.goto(`/meetings?series=${encodeURIComponent(seriesKey)}`);
  await expect(
    page.getByRole("link", { name: "最新会议", exact: true }),
  ).toBeVisible();
}

async function removeLatest(page: Page) {
  await page.getByRole("button", { name: "删除", exact: true }).click();
  await page.getByRole("button", { name: "确认删除", exact: true }).click();
}

async function releaseHeld(control: Awaited<ReturnType<typeof fixture>>) {
  const held = control.held.splice(0);
  await Promise.all(
    held.map(({ route, payload }) => route.fulfill({ json: payload })),
  );
}

for (const admin of [true, false]) {
  test(`历史导入旧链接返回会议列表，不显示入口或请求导入数据（${admin ? "管理员" : "普通员工"}）`, async ({
    page,
    context,
  }) => {
    const control = await fixture(context, admin);
    await page.goto("/validation?candidate=retired-record&upload=1");
    await expect(
      page.getByRole("heading", { name: "会议纪要与任务单", exact: true }),
    ).toBeVisible();
    expect(new URL(page.url()).pathname).toBe("/meetings");
    await expect(
      page.getByRole("link", { name: "导入历史会议", exact: true }),
    ).toHaveCount(0);
    await expect(
      page.getByRole("button", { name: "导入历史会议", exact: true }),
    ).toHaveCount(0);
    await expect(
      page.getByRole("button", { name: "＋ 导入 Excel 文件", exact: true }),
    ).toHaveCount(0);
    expect(control.unexpected).toEqual([]);
    expect(control.errors).toEqual([]);
  });
}

test("主页面没有历史入口，系列内原地展开历史会议，电脑手机都可收起", async ({
  page,
  context,
}) => {
  const control = await fixture(context);
  await page.goto("/meetings");
  await expect(
    page.getByRole("button", { name: "历史会议", exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("link", { name: "历史会议", exact: true }),
  ).toHaveCount(0);
  await page.getByRole("button", { name: "信息部周例会", exact: true }).click();
  await expect(
    page.getByRole("link", { name: "最新会议", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("link", { name: "上次会议", exact: true }),
  ).toHaveCount(0);
  expect(control.reads.length).toBeGreaterThanOrEqual(2);
  expect(
    control.reads.every(
      (search) => new URLSearchParams(search).get("view") === "current",
    ),
  ).toBe(true);
  const history = page.getByRole("button", { name: "历史会议", exact: true });
  await expect(history).toHaveAttribute("aria-expanded", "false");
  const url = page.url();
  const table = page.locator("main .el-table").last();
  const [tableBox, historyBox] = await Promise.all([
    table.boundingBox(),
    history.boundingBox(),
  ]);
  expect(historyBox!.y).toBeGreaterThanOrEqual(tableBox!.y + tableBox!.height);
  await history.click();
  await expect(history).toHaveAttribute("aria-expanded", "true");
  await expect(
    page.getByRole("link", { name: "上次会议", exact: true }),
  ).toBeVisible();
  const region = page.getByRole("region", {
    name: "本系列历史会议",
    exact: true,
  });
  await expect(
    region.getByRole("link", { name: "最新会议", exact: true }),
  ).toHaveCount(0);
  expect(page.url()).toBe(url);
  const request = new URLSearchParams(control.reads.at(-1));
  expect(request.get("view")).toBe("history");
  expect(request.get("series_key")).toBe(seriesKey);
  await expect(
    page
      .locator("aside")
      .getByRole("link", { name: "导入历史会议", exact: true }),
  ).toHaveCount(0);
  await page.screenshot({
    path: path.join(evidence, "current-desktop.png"),
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(history).toBeVisible();
  await expect(
    region.getByRole("link", { name: "上次会议", exact: true }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: path.join(evidence, "current-mobile.png"),
    fullPage: true,
  });
  await history.click();
  await expect(history).toHaveAttribute("aria-expanded", "false");
  await expect(
    page.getByRole("region", { name: "本系列历史会议", exact: true }),
  ).toHaveCount(0);
  expect(page.url()).toBe(url);
  expect(control.errors).toEqual([]);
  expect(control.unexpected).toEqual([]);
});

test("历史加载失败可在原位置重试，最新会议一直保留", async ({
  page,
  context,
}) => {
  const control = await fixture(context);
  await open(page);
  const url = page.url();
  control.next.push("fail");
  await page.getByRole("button", { name: "历史会议", exact: true }).click();
  const region = page.getByRole("region", {
    name: "本系列历史会议",
    exact: true,
  });
  await expect(region.getByRole("alert")).toContainText("刷新暂不可用");
  await expect(
    page.getByRole("link", { name: "最新会议", exact: true }),
  ).toBeVisible();
  await region
    .getByRole("button", { name: "重试加载历史会议", exact: true })
    .click();
  await expect(
    region.getByRole("link", { name: "上次会议", exact: true }),
  ).toBeVisible();
  expect(page.url()).toBe(url);
  expect(control.errors).toEqual([]);
  expect(control.unexpected).toEqual([]);
});

test("历史会议原地分页，删除后立即移除且不改变最新会议", async ({
  page,
  context,
}) => {
  const control = await fixture(context);
  control.rows.push(
    ...Array.from({ length: 20 }, (_, index) =>
      row(`old-${index}`, `往期会议${index}`, "2026-10-14"),
    ),
  );
  await open(page);
  await page.getByRole("button", { name: "历史会议", exact: true }).click();
  const region = page.getByRole("region", {
    name: "本系列历史会议",
    exact: true,
  });
  await expect(
    region.getByRole("link", { name: "上次会议", exact: true }),
  ).toBeVisible();
  await region.locator(".el-pagination .btn-next").click();
  await expect(
    region.getByRole("link", { name: "往期会议19", exact: true }),
  ).toBeVisible();
  await expect(
    region.getByRole("link", { name: "上次会议", exact: true }),
  ).toHaveCount(0);
  expect(new URLSearchParams(control.reads.at(-1)).get("offset")).toBe("20");
  await region.getByRole("button", { name: "删除", exact: true }).click();
  await page.getByRole("button", { name: "确认删除", exact: true }).click();
  await expect.poll(() => control.deletes).toEqual(["old-19"]);
  await expect(
    region.getByRole("link", { name: "往期会议19", exact: true }),
  ).toHaveCount(0);
  await expect(
    region.getByRole("link", { name: "上次会议", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("link", { name: "最新会议", exact: true }),
  ).toBeVisible();
  expect(control.errors).toEqual([]);
  expect(control.unexpected).toEqual([]);
});

test("历史空列表可收起，请求途中收起再打开不会残留加载状态", async ({
  page,
  context,
}) => {
  const control = await fixture(context);
  control.rows = control.rows.slice(0, 1);
  await open(page);
  const toggle = page.getByRole("button", { name: "历史会议", exact: true });
  control.next.push("hold");
  await toggle.click();
  await expect.poll(() => control.held.length).toBe(1);
  await toggle.click();
  await releaseHeld(control);
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
  await toggle.focus();
  await page.keyboard.press("Enter");
  const region = page.getByRole("region", {
    name: "本系列历史会议",
    exact: true,
  });
  await expect(
    region
      .locator(".meeting-history-table")
      .getByText("本系列暂无历史会议", { exact: true }),
  ).toBeVisible();
  await expect(region).toHaveAttribute("aria-busy", "false");
  await expect(
    page.getByRole("link", { name: "最新会议", exact: true }),
  ).toBeVisible();
  expect(control.errors).toEqual([]);
});

test("删除成功后刷新仍在途中时行立刻消失，返回后提升上一场", async ({
  page,
  context,
}) => {
  const control = await fixture(context);
  await open(page);
  control.next.push("hold");
  await removeLatest(page);
  await expect.poll(() => control.deletes.length).toBe(1);
  await expect.poll(() => control.held.length).toBe(1);
  await expect(
    page.getByRole("link", { name: "最新会议", exact: true }),
  ).toHaveCount(0);
  await releaseHeld(control);
  await expect(
    page.getByRole("link", { name: "上次会议", exact: true }),
  ).toBeVisible();
  expect(control.errors).toEqual([]);
});

test("删除后的后台刷新失败仍移除行，恢复焦点时补齐最新记录", async ({
  page,
  context,
}) => {
  const control = await fixture(context);
  await open(page);
  control.next.push("fail");
  await removeLatest(page);
  await expect(
    page.getByRole("link", { name: "最新会议", exact: true }),
  ).toHaveCount(0);
  await expect(page.locator("main")).not.toContainText("会议列表加载失败");
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await expect(
    page.getByRole("link", { name: "上次会议", exact: true }),
  ).toBeVisible();
  expect(control.errors).toEqual([]);
});

test("删除前发出的旧请求晚到不会让已删除会议重新出现", async ({
  page,
  context,
}) => {
  const control = await fixture(context);
  await open(page);
  control.next.push("hold");
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await expect.poll(() => control.held.length).toBe(1);
  await removeLatest(page);
  await expect(
    page.getByRole("link", { name: "上次会议", exact: true }),
  ).toBeVisible();
  const response = page.waitForResponse(
    (item) => new URL(item.url()).pathname === "/api/meetings",
  );
  await releaseHeld(control);
  await (await response).finished();
  await page.evaluate(
    () =>
      new Promise((resolve) =>
        requestAnimationFrame(() => requestAnimationFrame(resolve)),
      ),
  );
  await expect(
    page.getByRole("link", { name: "最新会议", exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("link", { name: "上次会议", exact: true }),
  ).toBeVisible();
  expect(control.errors).toEqual([]);
});

test("删除被拒绝时保留原行与用户筛选", async ({ page, context }) => {
  const control = await fixture(context);
  await open(page);
  const search = page.getByRole("textbox", { name: "搜索会议", exact: true });
  await search.fill("最新");
  await page.getByRole("button", { name: "查询", exact: true }).click();
  control.failDelete = true;
  await removeLatest(page);
  await expect(
    page.getByRole("link", { name: "最新会议", exact: true }),
  ).toBeVisible();
  await expect(search).toHaveValue("最新");
  expect(control.deletes).toEqual([]);
  expect(control.errors).toEqual([]);
});

test("草稿删除后立即刷新读到旧列表也不会让草稿重新出现", async ({
  page,
  context,
}) => {
  const control = await fixture(context);
  control.rows[0] = {
    ...control.rows[0],
    published_version: 0,
    has_draft: true,
    can_continue: false,
  };
  await open(page);
  control.staleDeleteReads = 10;
  const refreshed = page.waitForResponse(
    (response) => new URL(response.url()).pathname === "/api/meetings",
  );
  await removeLatest(page);
  await (await refreshed).finished();
  await page.evaluate(
    () => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))),
  );
  await expect(
    page.getByRole("link", { name: "最新会议", exact: true }),
  ).toHaveCount(0);
  control.staleDeleteReads = 0;
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await expect(
    page.getByRole("link", { name: "上次会议", exact: true }),
  ).toBeVisible();
  expect(control.errors).toEqual([]);
  expect(control.unexpected).toEqual([]);
});

test("另一标签成功删除后自动同步，同时保留查询条件", async ({
  page,
  context,
}) => {
  const control = await fixture(context);
  await open(page);
  const search = page.getByRole("textbox", { name: "搜索会议", exact: true });
  await search.fill("会议");
  await page.getByRole("button", { name: "查询", exact: true }).click();
  const other = await context.newPage();
  await open(other);
  await other.evaluate(async () => {
    const { api } = await import("/src/api.ts");
    await api("/meetings/latest", "DELETE", { expected_draft_version: 1 });
  });
  await expect(
    page.getByRole("link", { name: "上次会议", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("link", { name: "最新会议", exact: true }),
  ).toHaveCount(0);
  await expect(search).toHaveValue("会议");
  expect(control.errors).toEqual([]);
  expect(control.unexpected).toEqual([]);
});
