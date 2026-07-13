import type { CafeFeatureCollection, CafeProperties } from "./cafe-provider";

export interface FreshnessLimits {
  freshMaxAgeMinutes: number;
  displayMaxAgeMinutes: number;
}

export function currentObservationAgeMinutes(
  cafe: Pick<
    CafeProperties,
    "observedAt" | "observationAgeMinutes" | "observationAgeMeasuredAtMs"
  >,
  nowMs = Date.now(),
): number | null {
  if (
    cafe.observationAgeMinutes !== null &&
    Number.isFinite(cafe.observationAgeMeasuredAtMs) &&
    cafe.observationAgeMeasuredAtMs <= nowMs
  ) {
    return cafe.observationAgeMinutes +
      (nowMs - cafe.observationAgeMeasuredAtMs) / 60_000;
  }

  if (cafe.observedAt !== null) {
    const observedAtMs = Date.parse(cafe.observedAt);
    if (Number.isFinite(observedAtMs)) {
      if (observedAtMs > nowMs) return null;
      return (nowMs - observedAtMs) / 60_000;
    }
  }

  return cafe.observationAgeMinutes;
}

function ageCafe(
  cafe: CafeProperties,
  nowMs: number,
  limits: FreshnessLimits | null,
): CafeProperties {
  const ageMinutes = currentObservationAgeMinutes(cafe, nowMs);
  if (
    limits === null ||
    ageMinutes === null ||
    cafe.freshness === "n/a" ||
    cafe.freshness === "stale"
  ) {
    return { ...cafe, observationAgeMinutes: ageMinutes };
  }

  if (ageMinutes > limits.displayMaxAgeMinutes) {
    return {
      ...cafe,
      freshness: "stale",
      level: null,
      confidence: null,
      confidenceTier: null,
      observationAgeMinutes: ageMinutes,
    };
  }

  if (ageMinutes > limits.freshMaxAgeMinutes) {
    return {
      ...cafe,
      freshness: "delayed",
      confidence: null,
      confidenceTier: null,
      observationAgeMinutes: ageMinutes,
    };
  }

  return { ...cafe, freshness: "fresh", observationAgeMinutes: ageMinutes };
}

export function ageCafeCollection(
  collection: CafeFeatureCollection,
  nowMs = Date.now(),
  limits: FreshnessLimits | null = null,
): CafeFeatureCollection {
  return {
    ...collection,
    features: collection.features.map((feature) => ({
      ...feature,
      properties: ageCafe(feature.properties, nowMs, limits),
    })),
  };
}

export function hasVisualFreshnessChange(
  previous: CafeFeatureCollection,
  next: CafeFeatureCollection,
): boolean {
  if (previous.features.length !== next.features.length) return true;
  return previous.features.some((feature, index) => {
    const nextFeature = next.features[index];
    return nextFeature === undefined ||
      feature.properties.id !== nextFeature.properties.id ||
      feature.properties.freshness !== nextFeature.properties.freshness ||
      feature.properties.level !== nextFeature.properties.level;
  });
}
