const SEOUL_TIME_ZONE = "Asia/Seoul";
const MORNING_START_MINUTE = 7 * 60;
const MORNING_END_MINUTE = 10 * 60;
const EVENING_START_MINUTE = 17 * 60;
const EVENING_END_MINUTE = 20 * 60;

// Versioned static calendar. Unknown years fail closed so a missing calendar
// update cannot show a commute warning on an unverified public holiday.
const SEOUL_NON_COMMUTE_DATES = new Set([
  "2026-01-01",
  "2026-02-16",
  "2026-02-17",
  "2026-02-18",
  "2026-03-01",
  "2026-03-02",
  "2026-05-01",
  "2026-05-05",
  "2026-05-24",
  "2026-05-25",
  "2026-06-03",
  "2026-06-06",
  "2026-07-17",
  "2026-08-15",
  "2026-08-17",
  "2026-09-24",
  "2026-09-25",
  "2026-09-26",
  "2026-10-03",
  "2026-10-05",
  "2026-10-09",
  "2026-12-25",
]);
const SUPPORTED_CALENDAR_YEARS = new Set(["2026"]);

interface SeoulClock {
  isoDate: string;
  minuteOfDay: number;
  dayOfWeek: number;
  year: string;
}

export interface CommuteNoticeController {
  destroy(): void;
}

function seoulClock(now: Date): SeoulClock | null {
  if (!Number.isFinite(now.getTime())) return null;
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: SEOUL_TIME_ZONE,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).formatToParts(now);
  const values = Object.fromEntries(
    parts
      .filter(({ type }) => type !== "literal")
      .map(({ type, value }) => [type, value]),
  );
  const { year, month, day, hour, minute } = values;
  if (!year || !month || !day || !hour || !minute) return null;
  const isoDate = `${year}-${month}-${day}`;
  const minuteOfDay = Number(hour) * 60 + Number(minute);
  if (!Number.isFinite(minuteOfDay)) return null;
  const dayOfWeek = new Date(`${isoDate}T00:00:00Z`).getUTCDay();
  return { isoDate, minuteOfDay, dayOfWeek, year };
}

export function shouldShowCommuteNotice(now: Date): boolean {
  const clock = seoulClock(now);
  if (clock === null || !SUPPORTED_CALENDAR_YEARS.has(clock.year)) return false;
  if (
    clock.dayOfWeek === 0 ||
    clock.dayOfWeek === 6 ||
    SEOUL_NON_COMMUTE_DATES.has(clock.isoDate)
  ) return false;

  return (
    clock.minuteOfDay >= MORNING_START_MINUTE &&
    clock.minuteOfDay < MORNING_END_MINUTE
  ) || (
    clock.minuteOfDay >= EVENING_START_MINUTE &&
    clock.minuteOfDay < EVENING_END_MINUTE
  );
}

export function initializeCommuteNotice(
  now = new Date(),
): CommuteNoticeController {
  const notice = document.querySelector<HTMLElement>("#commute-notice");
  const closeButton = document.querySelector<HTMLButtonElement>("#commute-notice-close");
  if (!notice || !closeButton) {
    throw new Error("Required commute notice elements were not found");
  }

  notice.hidden = !shouldShowCommuteNotice(now);
  const dismiss = (): void => {
    notice.hidden = true;
  };
  closeButton.addEventListener("click", dismiss);

  return {
    destroy(): void {
      closeButton.removeEventListener("click", dismiss);
    },
  };
}
