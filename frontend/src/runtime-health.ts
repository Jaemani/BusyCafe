export type DataMode = "snapshot" | "live";
export type CycleStatus = "running" | "complete" | "partial" | "failed";

export interface RuntimeHealth {
  dataMode: DataMode;
  staleWarnMin: number;
  currentDisplayMaxAgeMin: number;
  lastCompleteCycleAt: string | null;
  lastCycleStatus: CycleStatus | null;
}

export type RuntimeHealthState =
  | { kind: "snapshot" }
  | { kind: "live" }
  | { kind: "delayed"; reason: "cycle" | "missing-time" | "invalid-time" | "stale" };

function readNullableString(value: unknown): string | null {
  return typeof value === "string" ? value : value === null ? null : null;
}

export async function fetchRuntimeHealth(): Promise<RuntimeHealth> {
  const response = await fetch("/api/health");
  if (!response.ok) throw new Error("데이터 상태 조회 실패");
  const payload = (await response.json()) as Record<string, unknown>;
  if (payload.data_mode !== "snapshot" && payload.data_mode !== "live") {
    throw new Error("데이터 상태 응답 형식 오류");
  }
  if (
    typeof payload.stale_warn_min !== "number" ||
    !Number.isInteger(payload.stale_warn_min) ||
    payload.stale_warn_min < 1
  ) {
    throw new Error("데이터 상태 임계값 오류");
  }
  if (
    typeof payload.current_display_max_age_min !== "number" ||
    !Number.isInteger(payload.current_display_max_age_min) ||
    payload.current_display_max_age_min <= payload.stale_warn_min
  ) {
    throw new Error("데이터 표시 임계값 오류");
  }
  const status = payload.last_cycle_status;
  if (
    status !== null &&
    status !== "running" &&
    status !== "complete" &&
    status !== "partial" &&
    status !== "failed"
  ) {
    throw new Error("데이터 cycle 상태 오류");
  }
  return {
    dataMode: payload.data_mode,
    staleWarnMin: payload.stale_warn_min,
    currentDisplayMaxAgeMin: payload.current_display_max_age_min,
    lastCompleteCycleAt: readNullableString(payload.last_complete_cycle_at),
    lastCycleStatus: status,
  };
}

export function evaluateRuntimeHealth(
  health: RuntimeHealth,
  nowMs = Date.now(),
): RuntimeHealthState {
  if (health.dataMode === "snapshot") return { kind: "snapshot" };
  if (health.lastCycleStatus === "partial" || health.lastCycleStatus === "failed") {
    return { kind: "delayed", reason: "cycle" };
  }
  if (health.lastCycleStatus === null) {
    return { kind: "delayed", reason: "cycle" };
  }
  if (health.lastCompleteCycleAt === null) {
    return { kind: "delayed", reason: "missing-time" };
  }
  const completedAtMs = Date.parse(health.lastCompleteCycleAt);
  if (!Number.isFinite(completedAtMs) || completedAtMs > nowMs) {
    return { kind: "delayed", reason: "invalid-time" };
  }
  const ageMin = (nowMs - completedAtMs) / 60_000;
  return ageMin > health.staleWarnMin
    ? { kind: "delayed", reason: "stale" }
    : { kind: "live" };
}
