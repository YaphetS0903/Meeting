import { onBeforeUnmount, onMounted } from "vue";
import { subscribeMeetingChanges } from "../meetingListSync";

interface MeetingListSyncOptions {
  intervalMs?: number;
  debounceMs?: number;
}

/** Refresh already-loaded lists without resetting their filters or pagination. */
export function useMeetingListSync(
  refresh: () => Promise<unknown>,
  { intervalMs = 15000, debounceMs = 150 }: MeetingListSyncOptions = {},
) {
  let stopped = true;
  let refreshing = false;
  let pending = false;
  let scheduled: ReturnType<typeof setTimeout> | undefined;
  let poll: ReturnType<typeof setInterval> | undefined;
  let unsubscribe = () => {};
  const visible = () => !stopped && document.visibilityState !== "hidden";

  function cancelScheduled() {
    if (scheduled !== undefined) clearTimeout(scheduled);
    scheduled = undefined;
  }

  async function refreshNow() {
    cancelScheduled();
    if (!visible()) return;
    if (refreshing) {
      pending = true;
      return;
    }
    refreshing = true;
    pending = false;
    try {
      await refresh();
    } catch {
      // Automatic checks retain the page's data; explicit loads report errors.
    } finally {
      refreshing = false;
      if (pending && visible()) schedule(0);
    }
  }

  function schedule(delay = debounceMs) {
    if (!visible()) return;
    if (refreshing) {
      pending = true;
      return;
    }
    // A burst is coalesced without indefinitely postponing the first refresh.
    if (scheduled === undefined) scheduled = setTimeout(refreshNow, delay);
  }

  function startPolling() {
    if (poll !== undefined) clearInterval(poll);
    poll = undefined;
    if (visible()) poll = setInterval(() => schedule(0), intervalMs);
  }

  function visibilityChanged() {
    startPolling();
    if (visible()) schedule(0);
    else {
      cancelScheduled();
      pending = false;
    }
  }

  const focused = () => schedule(0);
  const changed = () => schedule();

  onMounted(() => {
    stopped = false;
    unsubscribe = subscribeMeetingChanges(changed);
    document.addEventListener("visibilitychange", visibilityChanged);
    window.addEventListener("focus", focused);
    startPolling();
  });

  onBeforeUnmount(() => {
    stopped = true;
    pending = false;
    cancelScheduled();
    if (poll !== undefined) clearInterval(poll);
    unsubscribe();
    document.removeEventListener("visibilitychange", visibilityChanged);
    window.removeEventListener("focus", focused);
  });
}
