import { expect, test, type Locator, type Page } from "@playwright/test";
import { fileURLToPath } from "node:url";

const evidence = fileURLToPath(
  new URL(
    "../../../.validation/person-picker-filter-20261105/",
    import.meta.url,
  ),
);
const blocked = [
  "RPA机器人01",
  "RPA机器人02",
  "ceshi",
  "cs",
  "it机器人",
  "oa",
  "seeyon02",
  "RPA01",
  "测试账号02",
  "陈晓明",
];
async function harness(page: Page) {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.route("**/api/**", async (route) =>
    route.fulfill({
      json: {
        users: await page.evaluate(
          () => (window as any).personPickerHarness.users,
        ),
        departments: [{ id: "it", name: "信息部" }],
        series: [],
      },
    }),
  );
  await page.route("**/person-picker-audit", (route) =>
    route.fulfill({
      contentType: "text/html",
      body: '<meta name="viewport" content="width=device-width, initial-scale=1"><div id="harness"></div><script type="module" src="/tests/fixtures/person-picker-harness.ts"></script>',
    }),
  );
  await page.goto("/person-picker-audit");
  await expect(
    page.getByRole("heading", { name: "人员选择核查" }),
  ).toBeVisible();
  return errors;
}
async function paste(input: Locator, text: string) {
  await input.evaluate((node, value) => {
    const clipboardData = new DataTransfer();
    clipboardData.setData("text/plain", value);
    node.dispatchEvent(
      new ClipboardEvent("paste", {
        bubbles: true,
        cancelable: true,
        clipboardData,
      }),
    );
  }, text);
}
for (const width of [1440, 390]) {
  test(`所有共享人员入口过滤非人员账号，保留正常员工 ${width}px`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 1000 });
    const errors = await harness(page);
    for (const label of [
      "主持人",
      "编制人",
      "责任人",
      "验证人",
      "参会人员",
      "通知接收人",
      "权限授权员工",
    ]) {
      const input = page.getByRole("combobox", { name: label, exact: true });
      const list = page.getByRole("listbox", { name: label, exact: true });
      for (const name of blocked) {
        await input.fill(name);
        await expect(
          list.getByRole("option", { name, exact: true }),
        ).toHaveCount(0);
      }
      await input.fill("李四");
      await expect(
        list.getByRole("option", { name: "李四", exact: true }),
      ).toBeVisible();
      await list.getByRole("option", { name: "李四", exact: true }).click();
      await input.press("Escape");
      await page.getByRole("heading", { name: "人员选择核查" }).click();
    }
    const host = page.getByRole("combobox", { name: "主持人", exact: true });
    for (const name of ["Alice Chen", "谢龙兵"]) {
      await host.fill(name);
      await expect(
        page.getByRole("option", { name, exact: true }),
      ).toBeVisible();
    }
    await host.press("Escape");
    await page.screenshot({
      path: evidence + `pickers-${width}.png`,
      fullPage: true,
    });
    expect(errors).toEqual([]);
  });
}
test("粘贴和权限范围不绕过过滤，补录离职员工仍可用，旧选项保留且可移除", async ({
  page,
}) => {
  const errors = await harness(page);
  const owners = page.getByRole("combobox", { name: "责任人", exact: true });
  await paste(owners, "李四、RPA机器人01");
  await expect
    .poll(() =>
      page.evaluate(() => (window as any).personPickerHarness.models.owners),
    )
    .toEqual(["other"]);
  await owners.fill("王离职");
  await page
    .getByRole("option", { name: "王离职（已停用）", exact: true })
    .click();
  const verifier = page.getByRole("combobox", { name: "验证人", exact: true });
  await verifier.fill("王离职");
  await expect(
    page.getByRole("option", { name: "王离职（已停用）", exact: true }),
  ).toHaveCount(0);
  const recipients = page.getByRole("combobox", {
    name: "通知接收人",
    exact: true,
  });
  await recipients.fill("Alice");
  await expect(
    page.getByRole("option", { name: "Alice Chen", exact: true }),
  ).toHaveCount(0);
  const historical = page.locator(".people-select-field").filter({
    has: page.getByRole("combobox", { name: "历史已选账号", exact: true }),
  });
  await expect(historical).toContainText("RPA机器人01");
  await historical.locator(".el-tag__close").click();
  await expect
    .poll(() =>
      page.evaluate(
        () => (window as any).personPickerHarness.models.historical,
      ),
    )
    .toEqual([]);
  expect(errors).toEqual([]);
});
test("邮件抄送搜索及粘贴过滤服务账号，导入人员建议也过滤虚拟账号", async ({
  page,
}) => {
  const errors = await harness(page);
  const cc = page
    .getByRole("region", { name: "邮件抄送人" })
    .getByRole("combobox");
  for (const name of blocked) {
    await cc.fill(name);
    await expect(
      page.getByRole("option", { name: new RegExp(name) }),
    ).toHaveCount(0);
  }
  await paste(cc, "李四、RPA机器人01、extra@example.test");
  await expect
    .poll(() =>
      page.evaluate(() => (window as any).personPickerHarness.models.cc),
    )
    .toEqual(["other"]);
  await expect
    .poll(() =>
      page.evaluate(() => (window as any).personPickerHarness.models.emails),
    )
    .toEqual(["extra@example.test"]);
  await expect(page.locator(".smart-person-suggestion")).toHaveCount(1);
  await expect(page.locator(".smart-person-suggestion")).toContainText("李四");
  expect(errors).toEqual([]);
});
