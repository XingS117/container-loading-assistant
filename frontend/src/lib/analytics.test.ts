import {trackAnalyticsEvent} from './analytics';

afterEach(()=>{delete window.umami;vi.restoreAllMocks();});

test('analytics outages cannot break calculations or editing',()=>{
  window.umami={track:()=>{throw new Error('blocked');}};
  expect(()=>trackAnalyticsEvent('pack_calculation_started',{attempt_id:'abc'})).not.toThrow();
});

test('sends only approved operational fields, never orders, keys, notes or job capabilities',()=>{
  const track=vi.fn(); window.umami={track};
  trackAnalyticsEvent('pack_calculation_started',{attempt_id:'abc',input_id:'input',cargo_types:3,pieces:63,api_key:'secret',job_id:'capability',sku:'customer',note:'private',weight:200});
  expect(track).toHaveBeenCalledWith('pack_calculation_started',expect.objectContaining({attempt_id:'abc',input_id:'input',cargo_types:3,pieces:63,schema_version:2,journey_id:expect.any(String)}));
  expect(JSON.stringify(track.mock.calls)).not.toMatch(/secret|capability|customer|private|weight/);
  trackAnalyticsEvent('unknown_event',{pieces:5});
  expect(track).toHaveBeenCalledTimes(1);
});
