import { createApp, h, reactive } from "vue";
import ElementPlus from "element-plus";
import "element-plus/dist/index.css";
import PeopleSelect from "../../src/components/PeopleSelect.vue";
import MailCcSelect from "../../src/components/MailCcSelect.vue";
import ReviewPeopleField from "../../src/components/ReviewPeopleField.vue";
import { state } from "../../src/api";

const person = (id: string, name: string, extra = {}) => ({
  id,
  name,
  active: true,
  department_id: "it",
  email: `${id}@example.test`,
  ...extra,
});
const services = [
  "RPA机器人01",
  "RPA机器人02",
  "ceshi",
  "cs",
  "it机器人",
  "oa",
  "seeyon02",
  "RPA01",
  "测试账号02",
];
const users = [
  person("self", "张三"),
  person("other", "李四"),
  person("foreign", "Alice Chen"),
  person("engineer", "谢龙兵", { position: "机器人设备工程师" }),
  person("disabled", "王离职", { active: false }),
  person("virtual", "陈晓明", { position: "虚拟岗位" }),
  ...services.map((name, i) => person(`service-${i}`, name)),
];
state.me = users[0];
state.directory = {
  users,
  departments: [{ id: "it", name: "信息部" }],
  series: [],
};
const models = reactive<any>({
  host: "self",
  recorder: "self",
  owners: [],
  verifier: null,
  attendees: [],
  recipients: [],
  permission: null,
  historical: ["service-0"],
  cc: [],
  emails: [],
  review: null,
});
(window as any).personPickerHarness = { models, users };
const picker = (key: string, label: string, extra = {}) =>
  h("div", { style: "display:block;margin-bottom:20px" }, [
    h("span", { style: "display:block;margin-bottom:6px" }, label),
    h(PeopleSelect, {
      modelValue: models[key],
      "onUpdate:modelValue": (value: any) => (models[key] = value),
      ariaLabel: label,
      users: users.map(({ position, ...user }) => user),
      ...extra,
    }),
  ]);
createApp({
  render: () =>
    h("main", { style: "max-width:700px;padding:20px;margin:auto" }, [
      h("h1", "人员选择核查"),
      picker("host", "主持人"),
      picker("recorder", "编制人"),
      picker("owners", "责任人", {
        multiple: true,
        includeInactive: true,
        searchOnly: true,
        copyable: true,
      }),
      picker("verifier", "验证人", { searchOnly: true, copyable: true }),
      picker("attendees", "参会人员", { multiple: true }),
      picker("recipients", "通知接收人", {
        multiple: true,
        allowedIds: ["self", "other", "service-0"],
      }),
      picker("permission", "权限授权员工"),
      picker("historical", "历史已选账号", { multiple: true }),
      h("section", { "aria-label": "邮件抄送人" }, [
        h("span", "邮件抄送人"),
        h(MailCcSelect, {
          users,
          userIds: models.cc,
          emails: models.emails,
          "onUpdate:userIds": (value: string[]) => (models.cc = value),
          "onUpdate:emails": (value: string[]) => (models.emails = value),
        }),
      ]),
      h(ReviewPeopleField, {
        modelValue: models.review,
        needsAttention: true,
        suggestions: [users[1], users.at(-1), users[5]].map((user) => ({
          user_id: user!.id,
          name: user!.name,
          original_name: "原姓名",
          confidence: 0.9,
        })),
        "onUpdate:modelValue": (value: any) => (models.review = value),
      }),
    ]),
})
  .use(ElementPlus)
  .mount("#harness");
