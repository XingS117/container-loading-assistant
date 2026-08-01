type UmamiTracker = {
  track: (eventName?: string, eventData?: Record<string, string | number | boolean>) => void;
};

declare global {
  interface Window {
    umami?: UmamiTracker;
  }
}

let initialized = false;

export function initAnalytics(): void {
  if (initialized || typeof document === "undefined") return;

  const scriptUrl = import.meta.env.VITE_UMAMI_SCRIPT_URL?.trim();
  const websiteId = import.meta.env.VITE_UMAMI_WEBSITE_ID?.trim();
  if (!scriptUrl || !websiteId) return;

  const script = document.createElement("script");
  script.defer = true;
  script.src = scriptUrl;
  script.dataset.websiteId = websiteId;
  script.dataset.autoTrack = "true";
  document.head.appendChild(script);
  initialized = true;
}

export function trackAnalyticsEvent(eventName: string): void {
  if (typeof window === "undefined") return;
  window.umami?.track(eventName);
}
