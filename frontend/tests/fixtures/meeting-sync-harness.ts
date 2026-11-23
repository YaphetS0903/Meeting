import { createApp, h } from "vue";
import { api } from "../../src/api";
import { announceMeetingChange } from "../../src/meetingListSync";
import { useMeetingListSync } from "../../src/composables/useMeetingListSync";

const harness = {
  calls: 0,
  active: 0,
  maxActive: 0,
  block: false,
  failNext: false,
  release: () => {},
  visibility: "visible",
  storageEvents: [] as string[],
  fetchOptions: [] as { url: string; cache?: RequestCache }[],
  announce: announceMeetingChange,
  api,
  unmount: () => {},
};

Object.defineProperty(document, "visibilityState", {
  configurable: true,
  get: () => harness.visibility,
});
window.addEventListener("storage", (event) => {
  if (event.key === "meeting-list-change")
    harness.storageEvents.push(event.newValue || "");
});
const originalFetch = window.fetch;
window.fetch = (input, init) => {
  harness.fetchOptions.push({ url: String(input), cache: init?.cache });
  return originalFetch(input, init);
};

const app = createApp({
  setup() {
    useMeetingListSync(async () => {
      harness.calls += 1;
      harness.active += 1;
      harness.maxActive = Math.max(harness.maxActive, harness.active);
      try {
        if (harness.block)
          await new Promise<void>((resolve) => (harness.release = resolve));
        if (harness.failNext) {
          harness.failNext = false;
          throw new Error("Temporary background failure");
        }
      } finally {
        harness.active -= 1;
      }
    });
    return () => h("div", "Meeting list sync harness");
  },
});
app.mount("#harness");
harness.unmount = () => app.unmount();
(window as any).meetingSyncHarness = harness;
