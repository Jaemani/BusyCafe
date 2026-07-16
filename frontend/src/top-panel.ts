export interface TopPanelController {
  collapse(): void;
  destroy(): void;
  expand(): void;
}

interface StorageLike {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

export const TOP_PANEL_STORAGE_KEY = "busy-cafe:top-panel-collapsed";

function readCollapsed(storage: StorageLike | null): boolean {
  if (!storage) return false;
  try {
    return storage.getItem(TOP_PANEL_STORAGE_KEY) === "true";
  } catch {
    return false;
  }
}

function persistCollapsed(storage: StorageLike | null, collapsed: boolean): void {
  if (!storage) return;
  try {
    storage.setItem(TOP_PANEL_STORAGE_KEY, String(collapsed));
  } catch {
    // Storage may be disabled in private browsing. UI state still works.
  }
}

function browserStorage(): StorageLike | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

export function initializeTopPanel(
  shell: HTMLElement,
  header: HTMLElement,
  collapseButton: HTMLButtonElement,
  expandButton: HTMLButtonElement,
  storage: StorageLike | null = browserStorage(),
): TopPanelController {
  const setCollapsed = (collapsed: boolean, persist: boolean): void => {
    shell.dataset.collapsed = String(collapsed);
    header.hidden = collapsed;
    expandButton.hidden = !collapsed;
    collapseButton.setAttribute("aria-expanded", String(!collapsed));
    expandButton.setAttribute("aria-expanded", String(!collapsed));
    collapseButton.setAttribute("aria-label", "상단 검색 패널 접기");
    expandButton.setAttribute("aria-label", "상단 검색 패널 펼치기");
    if (persist) persistCollapsed(storage, collapsed);
  };

  const collapse = () => setCollapsed(true, true);
  const expand = () => setCollapsed(false, true);

  collapseButton.addEventListener("click", collapse);
  expandButton.addEventListener("click", expand);
  setCollapsed(readCollapsed(storage), false);

  return {
    collapse,
    destroy() {
      collapseButton.removeEventListener("click", collapse);
      expandButton.removeEventListener("click", expand);
    },
    expand,
  };
}
