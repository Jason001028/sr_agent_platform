// Isolated visual harness: actual Vue components, mocked stores/API, no SR.
import config from '../frontend/vite.config.ts';
const modules = {
  viewerContext: `export const snapshotWorkspace=()=>({version:1,items:[],total:27});`,
  chat: `import {reactive} from 'vue'; const store=reactive({sessionId:'fixture'}); export const useChatStore=()=>store;`,
  viewer: `import {reactive} from 'vue'; const store=reactive({recs:[],get recsOrdered(){return this.recs},sceneOrdinalOf:r=>r.id}); export const useViewerStore=()=>store;`,
  batchGoal: `export const useBatchGoalStore=()=>({snapshot:null,goalId:null});`,
  api: `export class HttpError extends Error{}; export const apiRepairStatus=async()=>({plan:window.fixturePlan,goal:null,items:[]}); export const apiConfirmRepair=async()=>{throw Error('Visual fixture only')}; export const apiStartRepair=apiConfirmRepair; export const apiRepairTargets=async()=>({items:window.fixturePlan.targets.items,has_more:false});`,
};
export default {...config, plugins:[{
  name:'repair-card-fixture', enforce:'pre',
  resolveId(source) { const name=source.replace(/\.(js|ts)$/,'').split('/').at(-1); if(modules[name] && /(?:stores|lib)\//.test(source)) return '\0fixture-'+name; },
  load(id) { if(id.startsWith('\0fixture-')) return modules[id.slice(9)]; },
}, ...config.plugins]};
