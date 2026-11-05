import {afterEach,beforeEach,describe,expect,it,vi} from 'vitest';
import {reactive,nextTick} from 'vue';
import {createPinia,setActivePinia} from 'pinia';
import type {ViewHistoryEntry} from '../../lib/viewHistory';
const mocks=vi.hoisted(()=>({viewer:null as any,tour:null as any}));
vi.mock('../viewer',()=>({useViewerStore:()=>mocks.viewer}));
vi.mock('../autoTour',()=>({useAutoTourStore:()=>mocks.tour}));
import {useViewHistoryStore} from '../viewHistory';
const state=(cx=.5,id=1):ViewHistoryEntry=>({mode:'off',active:'A',panes:[{side:'A',id,revision:'v1',cx,cy:.5,zoom:1}]});
async function settle(){await nextTick();await vi.advanceTimersByTimeAsync(300);await nextTick();}
beforeEach(()=>{
 vi.useFakeTimers();setActivePinia(createPinia());mocks.tour=reactive({status:'idle'});
 mocks.viewer=reactive({busy:false,drawMode:false,flickerPlaying:false,canvasAttached:true,viewAnimating:false,compareMode:'off',state:state(),
  captureNavigationView(){return this.state;},navigationViewAvailable:vi.fn(()=>true),stopViewAnim:vi.fn(),restoreNavigationView:vi.fn((entry:ViewHistoryEntry)=>{mocks.viewer.state=entry;return true;})});
});
afterEach(()=>vi.useRealTimers());
describe('settled view history',()=>{
 it('merges continuous zooms and holds long drags until release',async()=>{
  const h=useViewHistoryStore();h.connect();await settle();
  for(const x of [.6,.7,.8]){mocks.viewer.state=state(x);await nextTick();await vi.advanceTimersByTimeAsync(100);}await settle();expect(h.entries).toHaveLength(2);
  h.beginGesture();mocks.viewer.state=state(.9);await settle();mocks.viewer.state=state(1);await settle();expect(h.entries).toHaveLength(2);
  h.endGesture();await settle();expect(h.entries).toHaveLength(3);expect(h.entries[2].panes[0].cx).toBe(1);
 });
 it('restores identity and view, truncates forward branch after new input',async()=>{
  const h=useViewHistoryStore();h.connect();await settle();mocks.viewer.state=state(.7,2);await settle();expect(h.canBack).toBe(true);
  await h.go(-1);await settle();expect(mocks.viewer.restoreNavigationView).toHaveBeenCalledWith(state());expect(h.canForward).toBe(true);expect(h.entries).toHaveLength(2);
  mocks.viewer.state=state(.8,3);await settle();expect(h.canForward).toBe(false);expect(h.entries.map(e=>e.panes[0].id)).toEqual([1,3]);
 });
 it('excludes animation frames and suspended tours',async()=>{
  const h=useViewHistoryStore();h.connect();await settle();mocks.viewer.viewAnimating=true;mocks.viewer.state=state(.8);await settle();expect(h.entries).toHaveLength(1);
  mocks.viewer.viewAnimating=false;await settle();expect(h.entries).toHaveLength(2);
  mocks.tour.status='playing';mocks.viewer.state=state(2);await settle();expect(h.canBack).toBe(false);expect(h.entries).toHaveLength(2);
  mocks.tour.status='paused';await settle();expect(h.entries).toHaveLength(2);
 });
 it('skips unavailable images, resets mode context, and cancels unmounted timers',async()=>{
  const h=useViewHistoryStore();h.connect();await settle();mocks.viewer.state=state(.7,2);await settle();mocks.viewer.navigationViewAvailable.mockReturnValue(false);expect(h.canBack).toBe(false);
  mocks.viewer.compareMode='split';await settle();expect(h.entries).toHaveLength(1);
  mocks.viewer.state=state(.9,3);await nextTick();h.disconnect();await settle();expect(h.entries).toHaveLength(1);
 });
});
