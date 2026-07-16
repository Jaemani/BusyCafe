export type StreetFeedback = "busier" | "similar" | "quieter";
export type SeatFeedback = "available" | "limited" | "full" | "not_entered";
export type CafeReportType = "missing" | "wrong_details" | "closed";

export interface CafeContributionApi {
  submitCafeFeedback(
    cafeId: string,
    streetFeedback: StreetFeedback,
    seatFeedback: SeatFeedback,
  ): Promise<void>;
  reportCafe(cafeId: string, reportType: CafeReportType): Promise<void>;
  reportMissingCafe(reportedName: string): Promise<void>;
}

async function postJson(path: string, body: object, apiBaseUrl: string): Promise<void> {
  const response = await fetch(
    new URL(path, apiBaseUrl || window.location.origin),
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      cache: "no-store",
    },
  );
  if (!response.ok) throw new Error("요청을 전송하지 못했습니다");
}

export class HttpCafeContributionApi implements CafeContributionApi {
  constructor(private readonly apiBaseUrl = "") {}

  submitCafeFeedback(
    cafeId: string,
    streetFeedback: StreetFeedback,
    seatFeedback: SeatFeedback,
  ): Promise<void> {
    return postJson(
      `/api/cafes/${encodeURIComponent(cafeId)}/feedback`,
      { street_feedback: streetFeedback, seat_feedback: seatFeedback },
      this.apiBaseUrl,
    );
  }

  reportCafe(cafeId: string, reportType: CafeReportType): Promise<void> {
    return postJson(
      `/api/cafes/${encodeURIComponent(cafeId)}/report`,
      { report_type: reportType },
      this.apiBaseUrl,
    );
  }

  reportMissingCafe(reportedName: string): Promise<void> {
    return postJson(
      "/api/cafes/reports",
      { report_type: "missing", reported_name: reportedName },
      this.apiBaseUrl,
    );
  }
}
