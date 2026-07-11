import { showCafePanel } from "./panel";

export interface KakaoCafe {
  id: string;
  place_name: string;
  address_name: string;
  road_address_name: string;
  place_url: string;
  x: string;
  y: string;
}

interface KakaoLatLng {
  getLat(): number;
  getLng(): number;
}

interface KakaoBounds {
  getSouthWest(): KakaoLatLng;
  getNorthEast(): KakaoLatLng;
}

interface KakaoMapInstance {
  getBounds(): KakaoBounds;
  getLevel(): number;
}

interface KakaoMarkerInstance {
  setMap(map: KakaoMapInstance | null): void;
}

interface KakaoPagination {
  totalCount: number;
  last: number;
}

interface KakaoMapsNamespace {
  load(callback: () => void): void;
  LatLng: new (lat: number, lng: number) => KakaoLatLng;
  LatLngBounds: new (southWest: KakaoLatLng, northEast: KakaoLatLng) => KakaoBounds;
  Map: new (
    container: HTMLElement,
    options: { center: KakaoLatLng; level: number },
  ) => KakaoMapInstance;
  Marker: new (options: {
    map: KakaoMapInstance;
    position: KakaoLatLng;
    title: string;
  }) => KakaoMarkerInstance;
  event: {
    addListener(target: object, event: string, callback: () => void): void;
  };
  services: {
    Places: new () => {
      categorySearch(
        code: string,
        callback: (
          result: KakaoCafe[],
          status: string,
          pagination: KakaoPagination,
        ) => void,
        options: { bounds: KakaoBounds; page: number; size: number },
      ): void;
    };
    Status: { OK: string; ZERO_RESULT: string };
  };
}

declare global {
  interface Window {
    kakao?: { maps: KakaoMapsNamespace };
  }
}

interface SearchPage {
  cafes: KakaoCafe[];
  totalCount: number;
  lastPage: number;
}

const KAKAO_SDK_ID = "kakao-maps-sdk";
const CAFE_CATEGORY = "CE7";
const PAGE_SIZE = 15;
const MAX_PAGES = 3;
const MAX_SPLIT_DEPTH = 1;
const SEARCH_DEBOUNCE_MS = 350;

function loadKakaoSdk(apiKey: string): Promise<KakaoMapsNamespace> {
  return new Promise((resolve, reject) => {
    const existing = document.querySelector<HTMLScriptElement>(`#${KAKAO_SDK_ID}`);
    if (window.kakao?.maps) {
      window.kakao.maps.load(() => resolve(window.kakao!.maps));
      return;
    }

    const script = existing ?? document.createElement("script");
    script.id = KAKAO_SDK_ID;
    script.src = `https://dapi.kakao.com/v2/maps/sdk.js?appkey=${encodeURIComponent(apiKey)}&autoload=false&libraries=services`;
    script.addEventListener("load", () => {
      if (!window.kakao?.maps) {
        reject(new Error("Kakao Maps SDK가 초기화되지 않았습니다"));
        return;
      }
      window.kakao.maps.load(() => resolve(window.kakao!.maps));
    });
    script.addEventListener("error", () => reject(new Error("Kakao Maps SDK 로드 실패")));
    if (!existing) {
      document.head.append(script);
    }
  });
}

function splitBounds(maps: KakaoMapsNamespace, bounds: KakaoBounds): KakaoBounds[] {
  const sw = bounds.getSouthWest();
  const ne = bounds.getNorthEast();
  const midLat = (sw.getLat() + ne.getLat()) / 2;
  const midLng = (sw.getLng() + ne.getLng()) / 2;

  return [
    new maps.LatLngBounds(sw, new maps.LatLng(midLat, midLng)),
    new maps.LatLngBounds(
      new maps.LatLng(sw.getLat(), midLng),
      new maps.LatLng(midLat, ne.getLng()),
    ),
    new maps.LatLngBounds(
      new maps.LatLng(midLat, sw.getLng()),
      new maps.LatLng(ne.getLat(), midLng),
    ),
    new maps.LatLngBounds(new maps.LatLng(midLat, midLng), ne),
  ];
}

export async function initializeCafeMap(statusElement: HTMLElement): Promise<void> {
  const apiKey = import.meta.env.VITE_KAKAO_JS_KEY?.trim();
  if (!apiKey) {
    throw new Error("VITE_KAKAO_JS_KEY를 frontend/.env에 설정해주세요");
  }

  const container = document.querySelector<HTMLElement>("#map");
  if (!container) {
    throw new Error("지도 컨테이너가 없습니다");
  }

  const maps = await loadKakaoSdk(apiKey);
  const map = new maps.Map(container, {
    center: new maps.LatLng(37.5563, 126.9236),
    level: 5,
  });
  const places = new maps.services.Places();
  let markers: KakaoMarkerInstance[] = [];
  let refreshToken = 0;
  let debounceTimer: number | undefined;

  const searchPage = (bounds: KakaoBounds, page: number): Promise<SearchPage> =>
    new Promise((resolve, reject) => {
      places.categorySearch(
        CAFE_CATEGORY,
        (result, searchStatus, pagination) => {
          if (searchStatus === maps.services.Status.ZERO_RESULT) {
            resolve({ cafes: [], totalCount: 0, lastPage: 0 });
            return;
          }
          if (searchStatus !== maps.services.Status.OK) {
            reject(new Error("현재 화면의 카페 검색에 실패했습니다"));
            return;
          }
          resolve({
            cafes: result,
            totalCount: pagination.totalCount,
            lastPage: pagination.last,
          });
        },
        { bounds, page, size: PAGE_SIZE },
      );
    });

  const searchBounds = async (bounds: KakaoBounds, depth: number): Promise<KakaoCafe[]> => {
    const first = await searchPage(bounds, 1);
    if (first.totalCount > PAGE_SIZE * MAX_PAGES && depth < MAX_SPLIT_DEPTH) {
      const childResults = await Promise.all(
        splitBounds(maps, bounds).map((child) => searchBounds(child, depth + 1)),
      );
      return childResults.flat();
    }

    const pageCount = Math.min(MAX_PAGES, first.lastPage);
    const remaining = await Promise.all(
      Array.from({ length: Math.max(0, pageCount - 1) }, (_, index) =>
        searchPage(bounds, index + 2),
      ),
    );
    return [first.cafes, ...remaining.map((page) => page.cafes)].flat();
  };

  const renderCafes = (cafes: KakaoCafe[]): void => {
    markers.forEach((marker) => marker.setMap(null));
    markers = cafes.map((cafe) => {
      const marker = new maps.Marker({
        map,
        position: new maps.LatLng(Number(cafe.y), Number(cafe.x)),
        title: cafe.place_name,
      });
      maps.event.addListener(marker, "click", () => showCafePanel(cafe));
      return marker;
    });
  };

  const refresh = async (): Promise<void> => {
    const token = ++refreshToken;
    if (map.getLevel() > 8) {
      renderCafes([]);
      statusElement.textContent = "카페를 보려면 지도를 확대해주세요";
      return;
    }

    statusElement.textContent = "현재 화면의 카페를 찾는 중…";
    statusElement.dataset.state = "loading";
    try {
      const results = await searchBounds(map.getBounds(), 0);
      if (token !== refreshToken) return;

      const unique = [...new Map(results.map((cafe) => [cafe.id, cafe])).values()];
      renderCafes(unique);
      statusElement.textContent = `카페 ${unique.length.toLocaleString("ko-KR")}곳`;
      statusElement.dataset.state = "ready";
    } catch (error) {
      if (token !== refreshToken) return;
      statusElement.textContent =
        error instanceof Error ? error.message : "카페를 불러오지 못했습니다";
      statusElement.dataset.state = "error";
    }
  };

  const scheduleRefresh = (): void => {
    window.clearTimeout(debounceTimer);
    debounceTimer = window.setTimeout(() => void refresh(), SEARCH_DEBOUNCE_MS);
  };

  maps.event.addListener(map, "idle", scheduleRefresh);
  await refresh();
}
