// @vitest-environment jsdom
import { beforeEach, describe, expect, it } from "vitest";
import {
  initializeCommuteNotice,
  shouldShowCommuteNotice,
} from "./commute-notice";

describe("commute notice", () => {
  beforeEach(() => {
    document.body.innerHTML = `
      <aside id="commute-notice" hidden>
        <button id="commute-notice-close" type="button">닫기</button>
      </aside>`;
  });

  it("uses Seoul morning and evening commute windows", () => {
    expect(shouldShowCommuteNotice(new Date("2026-07-13T22:30:00Z"))).toBe(true);
    expect(shouldShowCommuteNotice(new Date("2026-07-14T09:30:00Z"))).toBe(true);
    expect(shouldShowCommuteNotice(new Date("2026-07-14T03:00:00Z"))).toBe(false);
    expect(shouldShowCommuteNotice(new Date("2026-07-14T11:00:00Z"))).toBe(false);
  });

  it("excludes weekends, public holidays, and unverified calendar years", () => {
    expect(shouldShowCommuteNotice(new Date("2026-07-10T23:00:00Z"))).toBe(false);
    expect(shouldShowCommuteNotice(new Date("2026-07-16T23:00:00Z"))).toBe(false);
    expect(shouldShowCommuteNotice(new Date("2027-07-14T23:00:00Z"))).toBe(false);
  });

  it("shows on each initialization and dismisses only the current page instance", () => {
    const commuteTime = new Date("2026-07-13T22:30:00Z");
    const first = initializeCommuteNotice(commuteTime);
    const notice = document.querySelector<HTMLElement>("#commute-notice")!;
    expect(notice.hidden).toBe(false);

    document.querySelector<HTMLButtonElement>("#commute-notice-close")!.click();
    expect(notice.hidden).toBe(true);
    first.destroy();

    const second = initializeCommuteNotice(commuteTime);
    expect(notice.hidden).toBe(false);
    second.destroy();
  });
});
