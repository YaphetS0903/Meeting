const CHANGE_EVENT = "meeting-list-changed";
const STORAGE_KEY = "meeting-list-change";

export function affectsMeetingLists(path: string) {
  const pathname = path.split("?", 1)[0];
  return (
    /^\/(meetings|meeting-series|series)(\/|$)/.test(pathname) ||
    /^\/validation\/[^/]+\/confirm$/.test(pathname)
  );
}

export function announceMeetingChange() {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new Event(CHANGE_EVENT));
  try {
    // Only a changing marker is shared; no meeting or account data leaves this tab.
    window.localStorage.setItem(STORAGE_KEY, `${Date.now()}:${Math.random()}`);
  } catch {
    // Restricted webviews still receive the local event and visible-page polling.
  }
}

export function subscribeMeetingChanges(changed: () => void) {
  if (typeof window === "undefined") return () => {};
  const storageChanged = (event: StorageEvent) => {
    if (event.key === STORAGE_KEY && event.newValue) changed();
  };
  window.addEventListener(CHANGE_EVENT, changed);
  window.addEventListener("storage", storageChanged);
  return () => {
    window.removeEventListener(CHANGE_EVENT, changed);
    window.removeEventListener("storage", storageChanged);
  };
}
