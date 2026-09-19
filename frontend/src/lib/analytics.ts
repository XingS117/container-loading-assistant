type UmamiTracker = {
  track: (eventName?: string, eventData?: Record<string, string | number | boolean>) => void | Promise<unknown>;
};

declare global {
  interface Window {
    umami?: UmamiTracker;
  }
}

let initialized = false;
let journeyId: string | undefined;
let eventSequence = 0;
type EventData = Record<string, string | number | boolean>;
const pending: Array<[string,EventData]> = [];
const fields: Record<string,string[]> = {
  pack_input_started: ['source'],
  pack_input_completed: ['cargo_types','pieces','preferred_profile'],
  pack_calculation_started: ['mode','cargo_types','pieces','preferred_profile'],
  pack_solutions_generated: ['mode','elapsed_ms','cargo_types','pieces','recommended_profile','budget_fallback'],
  pack_calculation_failed: ['mode','elapsed_ms','category','reason','last_phase'],
  pack_calculation_timeout: ['mode','elapsed_ms','reason','last_phase'],
  pack_layout_workbench_applied: ['profile','loaded_pieces','removed_pieces','moved_pieces'],
  pack_solution_selected: ['profile'],
  pack_export_print: ['profile','adjusted'],
  pack_solution_feedback: ['profile','result','adjusted'],
  pack_solution_adjustment_submitted: ['profile','topics','has_note'],
  pack_edit_input: [],
  cargo_preset_loaded: ['preset'],
  cargo_excel_import_failed: ['issue_count'],
  cargo_excel_imported: ['cargo_types','pieces'],
  order_copied: ['order_id'],
  order_restored: ['order_id'],
};

function send(event: [string,EventData]): void {
  try { Promise.resolve(window.umami?.track(...event)).catch(() => undefined); }
  catch { /* Analytics must never interrupt the loading workflow. */ }
}

function flush(): void {
  if (!window.umami) return;
  for (const event of pending.splice(0)) send(event);
}

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
  script.addEventListener('load',flush,{once:true});
  script.addEventListener('error',()=>{pending.length=0;},{once:true});
  document.head.appendChild(script);
  initialized = true;
}

export function trackAnalyticsEvent(eventName: string, eventData?: Record<string, string | number | boolean>): void {
  if (typeof window === "undefined") return;
  const allowed=Object.hasOwn(fields,eventName) ? fields[eventName] : undefined;
  if (!allowed) return;
  const safe: EventData = {schema_version:2,journey_id:journeyId ??= crypto.randomUUID(),event_seq:++eventSequence,occurred_at_ms:Date.now()};
  for (const key of ['input_id','attempt_id','is_example','source',...allowed]) {
    const value=eventData?.[key];
    if (typeof value==='number' && Number.isFinite(value) || typeof value==='boolean'
      || typeof value==='string' && value.length<=160) safe[key]=value;
  }
  if (window.umami) { flush(); send([eventName,safe]); }
  else if (initialized && pending.length<32) pending.push([eventName,safe]);
}
