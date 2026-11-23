import { expect, test, type Locator, type Page } from "@playwright/test";
import { fileURLToPath } from "node:url";
import { isEmployeeAccount } from "../src/people";

const evidence = fileURLToPath(
  new URL(
    "../../../.validation/dispatch-people-search-20261105/",
    import.meta.url,
  ),
);
const me = {
  id: "me",
  name: "袁可炜",
  active: true,
  is_admin: true,
  department_id: "it",
};
const accounts = [
  me,
  { id: "zhang", name: "张三", active: true, department_id: "it" },
  { id: "li", name: "李四", active: true, department_id: "it" },
  { id: "duplicate-it", name: "李珊", active: true, department_id: "it" },
  { id: "duplicate-sales", name: "李珊", active: true, department_id: "sales" },
  { id: "outside", name: "王五", active: true, department_id: "sales" },
  { id: "inactive", name: "离职员工", active: false, department_id: "it" },
  {
    id: "virtual",
    name: "陈晓明",
    position: "虚拟岗位",
    active: true,
    department_id: "it",
  },
  ...[
    "RPA机器人01",
    "RPA机器人02",
    "ceshi",
    "cs",
    "it机器人",
    "oa",
    "seeyon02",
    "RPA01",
    "测试账号02",
  ].map((name, i) => ({
    id: `service-${i}`,
    name,
    active: true,
    department_id: "it",
  })),
];

async function setup(page: Page, scoped = false, stale = false) {
  const writes: any[] = [],
    errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.addInitScript(
    ({ stale }) => {
      sessionStorage.setItem("token", "isolated-dispatch-people");
      if (stale)
        sessionStorage.setItem(
          "direct-assignment-attempt:me",
          JSON.stringify({
            key: "previous-attempt-123",
            payload: {
              content: "恢复上次的任务",
              owner_ids: ["service-0"],
              verifier_id: "me",
              due_date: "2026-11-20",
            },
          }),
        );
    },
    { stale },
  );
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname,
      method = route.request().method();
    let body: any;
    if (path === "/api/me") body = me;
    else if (path === "/api/auth/options") body = { wecom: false };
    else if (path === "/api/directory")
      body = {
        users: accounts,
        departments: [
          { id: "it", name: "信息部" },
          { id: "sales", name: "销售部" },
        ],
        series: [],
      };
    else if (path === "/api/direct-assignments/options")
      body = {
        can_assign: true,
        scope: scoped ? "department" : "all",
        native_todo_auto_confirm: false,
        wecom_bound: false,
        wecom_notifications_enabled: false,
        // Match production: active identities normally omit the active field.
        users: accounts
          .filter(
            (person) =>
              !scoped || ["zhang", "li", "service-0"].includes(person.id),
          )
          .map((person) => ({
            id: person.id,
            name: person.name,
            department_id: person.department_id,
            ...(person.active ? {} : { active: false }),
          })),
      };
    else if (path === "/api/direct-assignments") {
      if (method === "GET") body = [];
      else {
        expect(method).toBe("POST");
        writes.push(route.request().postDataJSON());
        return route.fulfill({
          status: 201,
          json: { id: "created", version: 1, wecom_notification_state: null },
        });
      }
    } else if (path === "/api/tasks/created/native-todo")
      body = { enabled: false, is_issuer: true };
    else throw new Error(`Unexpected API call ${method} ${path}`);
    await route.fulfill({ json: body });
  });
  await page.goto("/assignments");
  await expect(
    page.getByRole("combobox", { name: "选择责任人", exact: true }),
  ).toBeVisible();
  return { writes, errors };
}

async function paste(input: Locator, value: string) {
  await input.focus();
  await input.evaluate((node, text) => {
    const clipboardData = new DataTransfer();
    clipboardData.setData("text/plain", text);
    node.dispatchEvent(
      new ClipboardEvent("paste", {
        bubbles: true,
        cancelable: true,
        clipboardData,
      }),
    );
  }, value);
}

test("known service identities are hidden while real and foreign names are retained", () => {
  for (const name of [
    "RPA机器人01",
    "ceshi",
    "cs",
    "it机器人",
    "oa",
    "seeyon02",
    "RPA01",
    "测试账号02",
    " ＲＰＡ０１ ",
  ])
    expect(isEmployeeAccount({ name }), name).toBe(false);
  for (const name of [
    "朱慧铃",
    "袁可炜",
    "欧阳娜娜",
    "Alice Chen",
    "阿依古丽·艾力",
    "张三丰",
  ])
    expect(isEmployeeAccount({ name }), name).toBe(true);
  expect(isEmployeeAccount({ name: "陈晓明", position: "虚拟岗位" })).toBe(
    false,
  );
});

for (const width of [1366, 390]) {
  test(`responsible people and verifiers search, filter service accounts, paste and copy ${width}`, async ({
    page,
    context,
  }) => {
    await page.setViewportSize({ width, height: 950 });
    await context.grantPermissions(["clipboard-read", "clipboard-write"]);
    const result = await setup(page);
    for (const role of ["选择责任人", "选择验证人"]) {
      const input = page.getByRole("combobox", { name: role, exact: true });
      await input.click();
      await input.press("ArrowDown");
      await expect(page.locator(".el-select__popper:visible")).toHaveCount(0);
      for (const query of [
        "RPA",
        "机器人",
        "ceshi",
        "seeyon",
        "陈晓明",
        "离职",
      ]) {
        await input.fill(query);
        await expect(page.getByRole("option")).toHaveCount(0);
      }
      await input.fill("张");
      await expect(
        page.getByRole("option", { name: "张三", exact: true }),
      ).toBeVisible();
      await page.getByRole("option", { name: "张三", exact: true }).click();
      await expect(page.locator(".el-select__popper:visible")).toHaveCount(0);
      await input.focus();
      await input.press("Control+c");
      await expect
        .poll(() => page.evaluate(() => navigator.clipboard.readText()))
        .toBe("张三");
    }
    const owners = page.getByRole("combobox", {
      name: "选择责任人",
      exact: true,
    });
    await paste(owners, "李四、RPA机器人01");
    const field = page.locator(".people-select-field").filter({ has: owners });
    await expect(field.locator(".people-select-name")).toHaveText([
      "张三",
      "李四",
    ]);
    const verifier = page.getByRole("combobox", {
      name: "选择验证人",
      exact: true,
    });
    await paste(verifier, "李四");
    await expect(
      page
        .locator(".people-select-field")
        .filter({ has: verifier })
        .locator(".people-select-name"),
    ).toHaveText("李四");
    await page.screenshot({
      path: evidence + `selected-people-${width}.png`,
      fullPage: true,
    });
    await verifier.focus();
    await verifier.press("Control+c");
    await expect
      .poll(() => page.evaluate(() => navigator.clipboard.readText()))
      .toBe("李四");
    expect(result.errors).toEqual([]);
    expect(result.writes).toEqual([]);
  });
}

test("department scope is preserved and personally verifying remains possible", async ({
  page,
}) => {
  const result = await setup(page, true);
  for (const role of ["选择责任人", "选择验证人"]) {
    const input = page.getByRole("combobox", { name: role, exact: true });
    await input.fill("王五");
    await expect(page.getByRole("option")).toHaveCount(0);
    await paste(input, "王五");
    await input.fill("张");
    await expect(
      page.getByRole("option", { name: "张三", exact: true }),
    ).toBeVisible();
    await input.fill("");
  }
  const verifier = page.getByRole("combobox", {
    name: "选择验证人",
    exact: true,
  });
  await verifier.fill("袁");
  await expect(
    page.getByRole("option", { name: "袁可炜", exact: true }),
  ).toBeVisible();
  await page.getByRole("option", { name: "袁可炜", exact: true }).click();
  expect(result.writes).toEqual([]);
  expect(result.errors).toEqual([]);
});

test("same-name people require explicit choice and selected IDs reach confirmation", async ({
  page,
}) => {
  const result = await setup(page);
  const owners = page.getByRole("combobox", {
    name: "选择责任人",
    exact: true,
  });
  await owners.fill("李珊");
  await expect(page.getByRole("option")).toHaveCount(2);
  await page
    .getByRole("option", { name: "李珊 · 信息部", exact: true })
    .click();
  await paste(
    page.getByRole("combobox", { name: "选择验证人", exact: true }),
    "张三",
  );
  await page
    .getByRole("textbox", { name: "任务内容", exact: true })
    .fill("核对派发对象");
  const due = page.getByRole("combobox", { name: "计划完成时间", exact: true });
  await due.fill("2026-11-20");
  await due.press("Enter");
  await page.getByRole("button", { name: "确认派发任务", exact: true }).click();
  await page
    .getByRole("dialog", { name: "确认派发任务", exact: true })
    .getByRole("button", { name: "确认派发", exact: true })
    .click();
  await expect.poll(() => result.writes.length).toBe(1);
  expect(result.writes[0]).toMatchObject({
    owner_ids: ["duplicate-it"],
    verifier_id: "zhang",
    content: "核对派发对象",
    due_date: "2026-11-20",
  });
  expect(result.errors).toEqual([]);
});

test("restored service-account recipients must be corrected before dispatch", async ({
  page,
}) => {
  const result = await setup(page, false, true);
  await page.getByRole("button", { name: "确认派发任务", exact: true }).click();
  await expect(
    page.getByText("责任人不在当前可选员工范围，请重新选择", { exact: true }),
  ).toBeVisible();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  expect(result.writes).toEqual([]);
});
