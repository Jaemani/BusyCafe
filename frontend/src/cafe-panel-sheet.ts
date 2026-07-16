export type CafePanelSheetController = {
  collapse(): void;
  destroy(): void;
  expand(): void;
};

let activeController: CafePanelSheetController | null = null;

function setExpanded(
  panel: HTMLElement,
  toggle: HTMLButtonElement,
  expanded: boolean,
): void {
  panel.dataset.sheetState = expanded ? "expanded" : "compact";
  toggle.setAttribute("aria-expanded", String(expanded));
  toggle.setAttribute(
    "aria-label",
    expanded ? "카페 상세 접기" : "카페 상세 펼치기",
  );
  if (!expanded) panel.scrollTop = 0;
}

export function initializeCafePanelSheet(
  panel: HTMLElement,
  toggle: HTMLButtonElement,
): CafePanelSheetController {
  activeController?.destroy();

  const expand = () => setExpanded(panel, toggle, true);
  const collapse = () => setExpanded(panel, toggle, false);
  const onToggle = () => {
    if (toggle.getAttribute("aria-expanded") === "true") {
      collapse();
    } else {
      expand();
    }
  };
  const onKeydown = (event: KeyboardEvent) => {
    if (event.key !== "Escape" || panel.dataset.sheetState !== "expanded") return;
    collapse();
    toggle.focus();
  };

  toggle.addEventListener("click", onToggle);
  panel.addEventListener("keydown", onKeydown);
  collapse();

  const controller: CafePanelSheetController = {
    collapse,
    destroy() {
      toggle.removeEventListener("click", onToggle);
      panel.removeEventListener("keydown", onKeydown);
      if (activeController === controller) activeController = null;
    },
    expand,
  };
  activeController = controller;
  return controller;
}

export function collapseCafePanelSheet(): void {
  activeController?.collapse();
}
