// @vitest-environment jsdom
import {describe,it,expect,vi} from 'vitest';
import {createApp,reactive,nextTick} from 'vue';
const mocks=vi.hoisted(()=>({state:null as any}));
vi.mock('../batchQuality',()=>({useBatchQualityStore:()=>mocks.state}));
import BatchQualityPanel from '../../components/BatchQualityPanel.vue';

describe('whole-scene no-candidate review controls',()=>{
  it('offers explicit scene review without a Job, fake region or Mask, and blocks a stale displayed pair',async()=>{
    const decide=vi.fn();mocks.state=reactive({
      reviews:[{review_id:'clean',item_id:'clean',ordinal:1,name:'scene',review_kind:'no-candidate',current:true,
        scene_decision:'pending',reasons:[],counts:{approved:0,pending:0,rejected:0}}],
      doc:{review_id:'clean',binding:{kind:'no-candidate'},regions:[],scene_decision:'pending',audit:[]},
      totals:{scenes:1,regions:0,pending:0,approved:0,rejected:0},comparisonReady:true,busy:false,loading:false,
      reviewer:'整景审核人',reason:'已核对',error:'',refresh:vi.fn(),decide,openRegion:vi.fn(),flicker:vi.fn(),
    });
    const host=document.createElement('div');document.body.append(host);const app=createApp(BatchQualityPanel);app.mount(host);
    try{
      expect(host.textContent).toContain('无候选整景复核');expect(host.textContent).toContain('未执行 SR');
      expect(host.querySelector('[aria-label="选择待验收区域"]')).toBeNull();
      expect(host.querySelector('[aria-label="审核 Mask 显示"]')).toBeNull();
      const approve=[...host.querySelectorAll('button')].find(b=>b.textContent==='整景确认通过')!;
      expect(approve.disabled).toBe(false);approve.click();expect(decide).toHaveBeenCalledWith('scene','approved');
      mocks.state.comparisonReady=false;await nextTick();expect(approve.disabled).toBe(true);
    }finally{app.unmount();host.remove();}
  });
});
