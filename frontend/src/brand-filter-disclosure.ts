export interface BrandFilterDisclosureController {
  close(): void;
  destroy(): void;
  open(): void;
  toggle(): void;
}

function setOpen(
  filters: HTMLElement,
  toggleButton: HTMLButtonElement,
  open: boolean,
): void {
  filters.hidden = !open;
  toggleButton.setAttribute("aria-expanded", String(open));
  toggleButton.setAttribute(
    "aria-label",
    open ? "프랜차이즈 필터 닫기" : "프랜차이즈 필터 열기",
  );
}

export function initializeBrandFilterDisclosure(
  filters: HTMLElement,
  toggleButton: HTMLButtonElement,
  topPanelCollapseButton: HTMLButtonElement,
): BrandFilterDisclosureController {
  const close = () => setOpen(filters, toggleButton, false);
  const open = () => setOpen(filters, toggleButton, true);
  const toggle = () => setOpen(filters, toggleButton, filters.hidden);
  const onTopPanelCollapse = () => close();

  toggleButton.addEventListener("click", toggle);
  topPanelCollapseButton.addEventListener("click", onTopPanelCollapse);
  close();

  return {
    close,
    destroy() {
      toggleButton.removeEventListener("click", toggle);
      topPanelCollapseButton.removeEventListener("click", onTopPanelCollapse);
    },
    open,
    toggle,
  };
}
