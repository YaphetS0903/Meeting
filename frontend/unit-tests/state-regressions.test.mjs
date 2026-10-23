import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import vm from "node:vm";
import ts from "typescript";
import * as vue from "vue";

const pages = new URL("../src/pages/", import.meta.url);

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}

async function flush() {
  for (let i = 0; i < 6; i += 1) {
    await Promise.resolve();
    await vue.nextTick();
  }
}

// Evaluate each real <script setup> with Vue's reactivity and in-memory services.
// No browser, server, storage, or external notification provider is involved.
function pageHarness(t, name, handleApi, initialId = "A", confirm) {
  const route = vue.reactive({
    params: name === "Meeting" ? { id: initialId } : {},
    query: {},
    path: name === "Meeting" ? `/meetings/${initialId}` : "/validation",
  });
  const calls = [],
    errors = [],
    notices = [],
    teardown = [];
  const timers = new Map();
  let nextTimer = 0;
  let updateGuard;
  let leaveGuard;
  const router = {
    async push(target) {
      const location = typeof target === "string" ? { path: target } : target;
      const match = location.path.match(/^\/meetings\/([^/]+)$/);
      const params = match ? { id: match[1] } : {};
      const from = { params: { ...route.params }, path: route.path };
      const to = { params, path: location.path };
      const samePage = !!from.params.id && !!to.params.id;
      const guard = samePage ? updateGuard : leaveGuard;
      if (guard && (await guard(to, from)) === false) return false;
      route.params = params;
      route.query = location.query || {};
      route.path = location.path;
      await vue.nextTick();
      return true;
    },
    replace(target) {
      return router.push(target);
    },
  };
  let id = 0;
  const apiModule = {
    state: vue.reactive({
      me: { id: "admin", is_admin: true, department_id: "department" },
      directory: { users: [], departments: [], series: [] },
    }),
    api(path, method = "GET", body) {
      calls.push({
        path,
        method,
        body: body && JSON.parse(JSON.stringify(body)),
      });
      return Promise.resolve(handleApi(path, method, body));
    },
    run: async (fn) => {
      try {
        return await fn();
      } catch (error) {
        errors.push(error);
        return null;
      }
    },
    clientId: () => `new-item-${++id}`,
    localDate: () => "2026-10-20",
    person: (value) => value,
    query: (value) => new URLSearchParams(value).toString(),
    statuses: ["已完成", "未完成", "未到期", "不统计", "关闭"],
  };
  const exposed =
    name === "Meeting"
      ? "meeting,draft,dirty,saving,saveError,load,save,openEditor,continueMeeting,removeMeeting"
      : "selected,payload,aiBusy,aiSuggestions,aiApplied,aiConfigured,saving,open,closeReview,recognizeCandidate,save";
  const script = fs
    .readFileSync(new URL(`${name}.vue`, pages), "utf8")
    .match(/<script setup lang="ts">([\s\S]*?)<\/script>/)[1];
  const output = ts.transpileModule(
    `${script}\nglobalThis.page = {${exposed}};`,
    {
      compilerOptions: {
        target: ts.ScriptTarget.ES2022,
        module: ts.ModuleKind.CommonJS,
      },
    },
  ).outputText;
  const setTimer = (fn) => {
    timers.set(++nextTimer, fn);
    return nextTimer;
  };
  const clearTimer = (value) => timers.delete(value);
  const context = vm.createContext({
    exports: {},
    require(specifier) {
      if (specifier === "vue")
        return {
          ...vue,
          onMounted() {},
          onBeforeUnmount: (fn) => teardown.push(fn),
          onUnmounted: (fn) => teardown.push(fn),
        };
      if (specifier === "vue-router")
        return {
          useRoute: () => route,
          useRouter: () => router,
          onBeforeRouteUpdate: (fn) => {
            updateGuard = fn;
          },
          onBeforeRouteLeave: (fn) => {
            leaveGuard = fn;
          },
        };
      if (specifier === "element-plus")
        return {
          ElMessage: Object.fromEntries(
            ["success", "info", "warning", "error"].map((kind) => [
              kind,
              (message) => notices.push({ kind, message }),
            ]),
          ),
          ElMessageBox: {
            confirm: confirm || (async () => true),
            alert: async (message) => notices.push({ kind: "alert", message }),
          },
        };
      if (specifier === "../api") return apiModule;
      if (specifier.endsWith(".vue") || specifier === "../workContent")
        return {};
      throw Error(`Unexpected dependency: ${specifier}`);
    },
    setTimeout: setTimer,
    clearTimeout: clearTimer,
    window: {
      addEventListener() {},
      removeEventListener() {},
      setTimeout: setTimer,
      clearTimeout: clearTimer,
    },
    URL,
    URLSearchParams,
  });
  const scope = vue.effectScope();
  scope.run(() =>
    vm.runInContext(output, context, { filename: `${name}.vue` }),
  );
  t.after(() => {
    teardown.forEach((fn) => fn());
    scope.stop();
  });
  return {
    page: context.page,
    route,
    router,
    calls,
    errors,
    notices,
    timers,
    async runAutosave() {
      const callbacks = [...timers.values()];
      timers.clear();
      callbacks.forEach((fn) => fn());
      await flush();
    },
  };
}

function meeting(id, published = true) {
  const draft = {
    title: `会议 ${id}`,
    host_id: "admin",
    recorder_id: "admin",
    department_id: "department",
    series_id: "series",
    kind: "department",
    meeting_date: "2026-10-20",
    attendee_ids: [],
    items: [],
    attachment_ids: [],
    recipient_ids: [],
    cc_ids: [],
  };
  return {
    id,
    title: draft.title,
    host_id: "admin",
    draft_version: 1,
    published_version: published ? 1 : 0,
    has_draft: true,
    can_edit: true,
    can_continue: published,
    can_delete: true,
    draft,
    versions: published ? [{ number: 1, snapshot: draft }] : [],
  };
}

test("late meeting saves cannot overwrite the newly loaded meeting", async (t) => {
  const saveResponse = deferred();
  const h = pageHarness(t, "Meeting", (path, method) => {
    if (method === "PUT") return saveResponse.promise;
    return meeting(path.split("/").at(-1), path !== "/meetings/B");
  });
  await flush();
  h.page.openEditor();
  h.page.draft.value.title = "A 的修订";
  const saving = h.page.save(false);
  // Simulate a reload/navigation already committed outside this component's guard.
  h.route.params.id = "B";
  await flush();
  saveResponse.resolve({ ...meeting("A"), draft_version: 2 });
  assert.equal(await saving, false);
  await h.runAutosave();
  assert.equal(h.page.meeting.value.id, "B");
  assert.equal(h.page.draft.value.title, "会议 B");
  assert.equal(h.page.dirty.value, false);
  assert.equal(h.calls.filter((call) => call.method === "PUT").length, 1);
});

test("a late meeting GET cannot replace the current meeting", async (t) => {
  const firstLoad = deferred();
  const h = pageHarness(t, "Meeting", (path) =>
    path === "/meetings/A" ? firstLoad.promise : meeting("B", false),
  );
  h.route.params.id = "B";
  await flush();
  firstLoad.resolve(meeting("A"));
  await flush();
  assert.equal(h.page.meeting.value.id, "B");
  assert.equal(h.page.draft.value.title, "会议 B");
});

test("same-component navigation waits for an in-flight save", async (t) => {
  const saved = deferred();
  const h = pageHarness(t, "Meeting", (path, method) =>
    method === "PUT" ? saved.promise : meeting(path.split("/").at(-1)),
  );
  await flush();
  h.page.openEditor();
  h.page.draft.value.title = "修改后的 A";
  const saving = h.page.save(false);
  const navigation = h.router.push("/meetings/B");
  await flush();
  assert.equal(h.route.params.id, "A");
  saved.resolve({ ...meeting("A"), draft_version: 2 });
  await saving;
  await navigation;
  await flush();
  assert.equal(h.route.params.id, "B");
  assert.equal(h.page.meeting.value.id, "B");
  assert.equal(h.errors.length, 0);
});

test(
  "edits made while saving are saved with the next version",
  { timeout: 1500 },
  async (t) => {
    const saved = deferred();
    let puts = 0;
    const h = pageHarness(t, "Meeting", (path, method) => {
      if (method !== "PUT") return meeting("A");
      puts += 1;
      return puts === 1 ? saved.promise : { ...meeting("A"), draft_version: 3 };
    });
    await flush();
    h.page.openEditor();
    h.page.draft.value.title = "先写的内容";
    const first = h.page.save(false);
    h.page.draft.value.title = "保存期间补充的内容";
    const second = h.page.save(false);
    saved.resolve({ ...meeting("A"), draft_version: 2 });
    assert.equal(await first, true);
    assert.equal(await second, true);
    const requests = h.calls.filter((call) => call.method === "PUT");
    assert.equal(requests.length, 2);
    assert.equal(requests[1].body.expected_version, 2);
    assert.equal(requests[1].body.draft.title, "保存期间补充的内容");
    assert.equal(h.page.dirty.value, false);
  },
);

test(
  "first save keeps concurrent edits across the /new address replacement",
  { timeout: 1500 },
  async (t) => {
    const created = deferred();
    const h = pageHarness(
      t,
      "Meeting",
      (path, method) => {
        if (method === "POST") return created.promise;
        if (method === "PUT")
          return { ...meeting("created", false), draft_version: 2 };
        throw Error(`Unexpected reload: ${path}`);
      },
      "new",
    );
    h.page.draft.value.title = "新建会议";
    const saving = h.page.save(false);
    h.page.draft.value.decisions = "保存期间追加的决议";
    created.resolve(meeting("created", false));
    assert.equal(await saving, true);
    assert.equal(h.route.params.id, "created");
    assert.equal(h.page.draft.value.decisions, "保存期间追加的决议");
    await h.runAutosave();
    const writes = h.calls.filter((call) => call.method !== "GET");
    assert.deepEqual(
      writes.map((call) => call.method),
      ["POST", "PUT"],
    );
    assert.equal(writes[1].path, "/meetings/created");
    assert.equal(writes[1].body.draft.decisions, "保存期间追加的决议");
    assert.equal(h.errors.length, 0);
  },
);

test("continuing a meeting settles the source save before creating its successor", async (t) => {
  const saved = deferred();
  const h = pageHarness(t, "Meeting", (path, method) => {
    if (method === "PUT") return saved.promise;
    if (method === "POST") return { id: "B" };
    return meeting(path.split("/").at(-1), path !== "/meetings/B");
  });
  await flush();
  h.page.openEditor();
  h.page.draft.value.title = "修改后的 A";
  const saving = h.page.save(false);
  const continuing = h.page.continueMeeting();
  await flush();
  assert.equal(
    h.calls.some((call) => call.method === "POST"),
    false,
  );
  saved.resolve({ ...meeting("A"), draft_version: 2 });
  await saving;
  await continuing;
  await flush();
  assert.equal(h.page.meeting.value.id, "B");
  assert.equal(
    h.calls.filter((call) => call.method === "POST")[0].path,
    "/meetings/A/continue",
  );
  assert.equal(h.calls.filter((call) => call.method === "PUT").length, 1);
});

test("referenced meetings are shown before deletion and no DELETE is sent", async (t) => {
  const h = pageHarness(t, "Meeting", (path) =>
    path.endsWith("deletion-impact")
      ? {
          blocking_meeting_count: 2,
          blocking_meetings: [{ id: "B", title: "本周会议" }],
          hidden_blocking_meeting_count: 1,
        }
      : meeting("A"),
  );
  await flush();
  await h.page.removeMeeting();
  assert.equal(
    h.calls.some((call) => call.method === "DELETE"),
    false,
  );
  assert.match(
    h.notices.find((item) => item.kind === "alert").message,
    /本周会议/,
  );
  assert.match(
    h.notices.find((item) => item.kind === "alert").message,
    /另有 1 场/,
  );
});

function candidate(id) {
  return {
    id,
    version: 1,
    state: "pending",
    original: {},
    corrected: {
      kind: "department",
      items: [
        { id: `${id}-item`, section: "unknown", status: "invalid" },
        { id: `${id}-untouched`, section: "review", status: "invalid" },
      ],
    },
    issues: [
      {
        code: "section_unknown",
        field_path: "items.0.section",
        resolved: false,
      },
      { code: "invalid_status", field_path: "items.0.status", resolved: false },
      { code: "invalid_status", field_path: "items.1.status", resolved: false },
    ],
  };
}

test("AI preserves manual corrections while still filling untouched fields", async (t) => {
  const response = deferred();
  const h = pageHarness(t, "Validation", (path) =>
    path.endsWith("ai-suggestions") ? response.promise : candidate("A"),
  );
  await h.page.open("A");
  const pending = h.page.recognizeCandidate();
  h.page.payload.value.items[0].status = "已完成";
  h.page.payload.value.items[0].section = "new";
  response.resolve({
    suggestions: [
      { id: "A-item", section: "review", status: "未完成" },
      { id: "A-untouched", status: "未到期" },
    ],
  });
  await pending;
  assert.equal(h.page.payload.value.items[0].status, "已完成");
  assert.equal(h.page.payload.value.items[0].section, "new");
  assert.equal(h.page.payload.value.items[1].status, "未到期");
  assert.equal(h.page.aiBusy.value, false);
});

test("closing review discards late AI results without errors", async (t) => {
  const response = deferred();
  const h = pageHarness(t, "Validation", (path) =>
    path.endsWith("ai-suggestions") ? response.promise : candidate("A"),
  );
  await h.page.open("A");
  const pending = h.page.recognizeCandidate();
  await h.page.closeReview();
  response.resolve({ suggestions: [{ id: "A-item", status: "未完成" }] });
  await pending;
  assert.equal(h.page.selected.value, null);
  assert.equal(h.page.payload.value, null);
  assert.equal(h.page.aiBusy.value, false);
  assert.equal(h.notices.length, 0);
});

test("AI from the previous candidate cannot clear a newer candidate's busy state", async (t) => {
  const a = deferred(),
    b = deferred();
  const h = pageHarness(t, "Validation", (path) => {
    const id = path.split("/")[2];
    return path.endsWith("ai-suggestions")
      ? id === "A"
        ? a.promise
        : b.promise
      : candidate(id);
  });
  await h.page.open("A");
  const first = h.page.recognizeCandidate();
  await h.page.closeReview();
  await h.page.open("B");
  const second = h.page.recognizeCandidate();
  a.resolve({ suggestions: [{ id: "A-item", status: "未完成" }] });
  await first;
  assert.equal(h.page.aiBusy.value, true);
  assert.equal(h.page.aiApplied.value, 0);
  b.resolve({ suggestions: [{ id: "B-item", status: "未到期" }] });
  await second;
  assert.equal(h.page.payload.value.items[0].status, "未到期");
  assert.equal(h.page.aiBusy.value, false);
  assert.equal(h.page.aiSuggestions.value["A-item"], undefined);
});

test("late review saves cannot change the next candidate's version", async (t) => {
  const saved = deferred();
  const h = pageHarness(t, "Validation", (path, method) =>
    method === "PUT" ? saved.promise : candidate(path.split("/")[2]),
  );
  await h.page.open("A");
  const saving = h.page.save();
  await h.page.closeReview();
  await h.page.open("B");
  saved.resolve({ version: 8 });
  assert.equal(await saving, false);
  assert.equal(h.page.selected.value.id, "B");
  assert.equal(h.page.selected.value.version, 1);
  assert.equal(
    h.calls.filter((call) => call.path === "/validation/B").length,
    1,
  );
});
