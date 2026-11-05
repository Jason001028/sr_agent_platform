// @vitest-environment jsdom
import { createApp, nextTick, markRaw } from 'vue';
import { createPinia, setActivePinia } from 'pinia';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
vi.mock('../../router/index.js', () => ({ default: {} }));
vi.mock('../../components/MiniNavigator.vue', () => ({ default: { render: () => null } }));
vi.mock('../../components/ViewerBackdrop.vue', () => ({ default: { render: () => null } }));
vi.mock('../../components/ViewerHistoryNavigation.vue', () => ({ default: { render: () => null } }));
vi.mock('../../components/AgentAnnotationOverlay.vue', () => ({ default: { render: () => null } }));
vi.mock('../../components/DiffOverlay.vue', () => ({ default: { render: () => null } }));
import TifCanvas from '../../components/TifCanvas.vue';
import { useViewerStore, type ViewerRec } from '../viewer';
let store: ReturnType<typeof useViewerStore>, root: HTMLDivElement, canvas: HTMLCanvasElement;
let app: ReturnType<typeof createApp>;
const fills: string[] = [];
const lines: number[][] = [];
const ctx: any = new Proxy({ fillStyle: '', fill() { fills.push(this.fillStyle); },
  lineTo(x: number, y: number) { lines.push([x,y]); },
  getImageData: () => ({ data: new Uint8ClampedArray(4) }),
}, { get(target, key) { return key in target ? Reflect.get(target,key) : (() => {}); } });
function mouse(type: string, x: number, y: number, button=0, detail=1) {
  const target = type === 'mousedown' || type === 'dblclick' || type === 'contextmenu' ? canvas : window;
  target.dispatchEvent(new MouseEvent(type, { clientX:x+20, clientY:y+30, button, detail, bubbles:true, cancelable:true }));
}
function click(x: number,y: number,detail=1) { mouse('mousedown',x,y,0,detail); mouse('mouseup',x,y); }
function key(type: string, key: string, code=key) { window.dispatchEvent(new KeyboardEvent(type,{key,code,bubbles:true,cancelable:true})); }
function triangle() { click(100,100); click(200,100); click(200,200); }
beforeEach(async () => {
  const pinia=createPinia(); setActivePinia(pinia);
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} });
  vi.stubGlobal('requestAnimationFrame', () => 1); vi.stubGlobal('cancelAnimationFrame', () => {});
  vi.spyOn(HTMLCanvasElement.prototype,'getContext').mockReturnValue(ctx);
  vi.spyOn(HTMLElement.prototype,'getBoundingClientRect').mockReturnValue({left:20,top:30,width:800,height:600,right:820,bottom:630,x:20,y:30,toJSON(){}});
  root=document.createElement('div'); document.body.append(root);
  app=createApp(TifCanvas); app.use(pinia); app.mount(root);
  store=useViewerStore();
  store.recs.push({id:1,name:'synthetic',W:1000,H:1000,thumb:markRaw(document.createElement('canvas')),maskRois:[]} as unknown as ViewerRec);
  store.activeId=1; store.setCanvasSize(800,600); store.view={scale:1,ox:0,oy:0};
  store.enterDraw(); await nextTick();
  canvas=root.querySelector('.view-canvas')!; fills.length=0; lines.length=0;
});
afterEach(() => { app.unmount(); root.remove(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
it('defaults to polygon; fills only dynamic closed predictions after two anchors without committing', async () => {
  click(100,100); mouse('mousemove',200,200); await nextTick();
  expect(fills).not.toContain('rgba(217,71,82,0.14)');
  click(200,100); mouse('mousemove',200,200); await nextTick();
  expect(fills).toContain('rgba(217,71,82,0.14)');
  expect(lines).toContainEqual([100,100]);
  expect(store.getRois()).toHaveLength(0); expect(store.drawTool).toBe('polygon');
});
it.each([0,1])('pans with Space-left or middle without changing anchors (button %s)', async button => {
  triangle(); const points=JSON.stringify(store.pendingPts);
  if (button===0) key('keydown',' ','Space');
  mouse('mousedown',300,300,button); mouse('mousemove',350,320,button);
  key('keydown','Enter'); mouse('contextmenu',350,320,2);
  mouse('mouseup',350,320,button); key('keyup',' ','Space'); await nextTick();
  expect(store.view.ox).toBe(50); expect(store.view.oy).toBe(20);
  expect(JSON.stringify(store.pendingPts)).toBe(points); expect(store.getRois()).toHaveLength(0);
  expect(store.hoverPt).toEqual([300,300]);
  click(400,350); expect(store.pendingPts![3]).toEqual([350,330]);
});
it('zooms around the cursor during drawing and recomputes preview without moving vertices', async () => {
  triangle(); const points=JSON.stringify(store.pendingPts);
  canvas.dispatchEvent(new WheelEvent('wheel',{clientX:320,clientY:330,deltaY:-100,bubbles:true,cancelable:true}));
  await nextTick(); expect(store.view.scale).toBe(1.2);
  expect(store.hoverPt![0]*1.2+store.view.ox).toBeCloseTo(300);
  expect(JSON.stringify(store.pendingPts)).toBe(points); expect(store.getRois()).toHaveLength(0);
});
it('undoes the last vertex and cancels only the draft', () => {
  triangle(); key('keydown','Enter'); triangle(); key('keydown','Backspace');
  expect(store.pendingPts).toHaveLength(2); key('keydown','Escape');
  expect(store.pendingPts).toBeNull(); expect(store.getRois()).toHaveLength(1); expect(store.drawMode).toBe(true);
});
it('snaps within 11 CSS pixels and closes without a duplicate vertex', async () => {
  triangle(); mouse('mousemove',105,102); await nextTick();
  expect(root.querySelector('.polygon-close-hint')).not.toBeNull();
  click(105,102); expect(store.getRois()[0]).toHaveLength(3); expect(store.pendingPts).toBeNull();
});
it('double click adds only the first click and right click closes', () => {
  click(100,100); click(200,100); click(200,200); click(200,200,2); mouse('dblclick',200,200,0,2);
  expect(store.getRois()[0]).toHaveLength(3);
  triangle(); mouse('contextmenu',250,250,2); expect(store.getRois()).toHaveLength(2);
});
it('uses the right pane origin and preserves the active draft when clicking the opposite pane', async () => {
  store.compareMode='split'; store.paneA=1; store.paneB=1; store.activeSide='B';
  store.view={scale:2,ox:10,oy:20}; await nextTick();
  click(450,100); expect(store.pendingPts![0]).toEqual([20,40]);
  mouse('mousemove',550,150); expect(store.hoverPt).toEqual([70,65]);
  click(100,100); expect(store.activeSide).toBe('B'); expect(store.pendingPts).toHaveLength(1);
  key('keydown',' ','Space'); mouse('mousedown',550,150); mouse('mousemove',570,160); mouse('mouseup',570,160); key('keyup',' ','Space');
  expect(store.pendingPts![0]).toEqual([20,40]); expect(store.hoverPt).toEqual([70,65]);
});
it('keeps rectangle routing and ignores shortcuts typed in inputs', () => {
  store.setDrawTool('rect'); mouse('mousedown',50,50); mouse('mousemove',150,160); mouse('mouseup',150,160);
  expect(store.getRois()[0]).toEqual([[50,50],[150,50],[150,160],[50,160]]);
  store.setDrawTool('polygon'); triangle(); const input=document.createElement('input'); root.append(input);
  input.dispatchEvent(new KeyboardEvent('keydown',{key:'Backspace',bubbles:true})); expect(store.pendingPts).toHaveLength(3);
});

it('keeps an active pan exclusive when another button is pressed and recovers after window blur', () => {
  triangle(); const points=JSON.stringify(store.pendingPts);
  mouse('mousedown',300,300,1); click(400,400); mouse('mousemove',320,310,1);
  expect(JSON.stringify(store.pendingPts)).toBe(points); expect(store.view.ox).toBe(20);
  window.dispatchEvent(new Event('blur')); mouse('mousemove',350,350);
  expect(store.view.ox).toBe(20); expect(JSON.stringify(store.pendingPts)).toBe(points);
  click(450,400); expect(store.pendingPts).toHaveLength(4);
});
it('measures start snapping in CSS pixels at different zooms', () => {
  triangle(); store.view={scale:4,ox:0,oy:0};
  expect(store.polygonCanCloseAt([102.5,100])).toBe(true);
  expect(store.polygonCanCloseAt([103,100])).toBe(false);
});
it('does not route a rejected product-pane click to the input image', async () => {
  store.recs.push({...store.recs[0],id:2,stageKind:'product',maskRois:[]} as ViewerRec);
  store.compareMode='split'; store.paneA=1; store.paneB=2; await nextTick();
  click(500,100); expect(store.activeSide).toBe('A'); expect(store.pendingPts).toBeNull();
  expect(store.getRois()).toHaveLength(0);
});
it('temporary panning never invokes delete or starts a rectangle', () => {
  for (const tool of ['rect','del'] as const) {
    store.setDrawTool(tool); const down=vi.spyOn(store,'onCanvasDownDraw');
    mouse('mousedown',300,300,1); mouse('mousemove',320,310,1); mouse('mouseup',320,310,1);
    expect(down).not.toHaveBeenCalled(); expect(store.pendingRect).toBeNull(); down.mockRestore();
  }
});
