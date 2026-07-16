import { afterEach, describe, expect, it, vi } from "vitest";
import { HttpCafeContributionApi } from "./api";

describe("cafe contribution API", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("posts only cafe id and the two bounded feedback enums", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true });
    vi.stubGlobal("fetch", fetchMock);
    const api = new HttpCafeContributionApi("https://example.test");

    await api.submitCafeFeedback("42", "quieter", "available");

    const [url, init] = fetchMock.mock.calls[0] as [URL, RequestInit];
    expect(url.toString()).toBe("https://example.test/api/cafes/42/feedback");
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual({
      street_feedback: "quieter",
      seat_feedback: "available",
    });
  });

  it("posts selected and missing-place reports without coordinates", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true });
    vi.stubGlobal("fetch", fetchMock);
    const api = new HttpCafeContributionApi("https://example.test");

    await api.reportCafe("cafe/unsafe", "wrong_details");
    await api.reportMissingCafe("없는 카페");

    const [selectedUrl, selectedInit] = fetchMock.mock.calls[0] as [URL, RequestInit];
    const [missingUrl, missingInit] = fetchMock.mock.calls[1] as [URL, RequestInit];
    expect(selectedUrl.toString()).toBe(
      "https://example.test/api/cafes/cafe%2Funsafe/report",
    );
    expect(JSON.parse(String(selectedInit.body))).toEqual({
      report_type: "wrong_details",
    });
    expect(missingUrl.toString()).toBe("https://example.test/api/cafes/reports");
    expect(JSON.parse(String(missingInit.body))).toEqual({
      report_type: "missing",
      reported_name: "없는 카페",
    });
  });

  it("rejects non-success responses", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false }));
    const api = new HttpCafeContributionApi("https://example.test");

    await expect(api.reportCafe("1", "closed")).rejects.toThrow(
      "요청을 전송하지 못했습니다",
    );
  });
});
