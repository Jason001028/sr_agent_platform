import { expect, it } from 'vitest';
import { captureViewerContext, registerViewerObservation, snapshotViewer } from '../viewerContext';
import type { ViewerRec } from '../../stores/viewer';

it('freezes the 12 selected / 2 excluded draft and isolates later switches', () => {
  const draft = {draft_id:'first',version:1,total:14,selected:12,excluded:2};
  const off = registerViewerObservation('repair_selection',()=>draft);
  const first = captureViewerContext(0);
  draft.draft_id='second'; draft.version++; draft.selected=4;
  const second = captureViewerContext(0);
  expect(first.repair_selection).toMatchObject({draft_id:'first',selected:12,excluded:2,version:1});
  expect(second.repair_selection).toMatchObject({draft_id:'second',selected:4,version:2});
  off();
  expect(captureViewerContext(0)).not.toHaveProperty('repair_selection');
});

it('old panel cleanup cannot unregister a newer panel provider',()=>{
  const old = registerViewerObservation('repair_selection',()=>({version:1}));
  const current = registerViewerObservation('repair_selection',()=>({version:2}));
  old();
  expect(captureViewerContext(0).repair_selection).toEqual({version:2});
  current();
});

it('gives local TIFFs stable session identity without conflating same names',()=>{
  const rec={id:1,name:'local.tif',W:100,H:200,token:1,file:{}} as ViewerRec;
  const capture=()=>snapshotViewer({activeRec:rec,selectedIds:[],panes:[]},0).current?.image_key;
  const key=capture();
  expect(key).toMatch(/^local-session:/);
  expect(capture()).toBe(key);
  rec.file={} as File;
  expect(capture()).not.toBe(key);
});
