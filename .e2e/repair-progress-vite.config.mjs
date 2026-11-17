import config from '../frontend/vite.config.ts';
const modules = {
  viewerContext: `export const snapshotWorkspace=()=>({version:1,items:[],total:27});`,
  chat: `export const useChatStore=()=>({sessionId:'fixture'});`,
  viewer: `export const useViewerStore=()=>({recsOrdered:[],sceneOrdinalOf:()=>0});`,
  batchGoal: `export const useBatchGoalStore=()=>({snapshot:null,goalId:'fixture',attach:async()=>true,selectGoal:async()=>true});`,
  api: `export class HttpError extends Error{}; export const apiRepairStatus=async()=>window.fixtureStatus; export const apiConfirmRepair=async()=>{throw Error('Read-only visual fixture')}; export const apiStartRepair=apiConfirmRepair; export const apiRepairTargets=apiConfirmRepair;`,
};
export default {...config, plugins:[{
  name:'repair-progress-fixture',enforce:'pre',
  resolveId(source){const name=source.replace(/\.(js|ts)$/,'').split('/').at(-1);if(modules[name]&&/(?:stores|lib)\//.test(source))return '\0progress-'+name;},
  load(id){if(id.startsWith('\0progress-'))return modules[id.slice(10)];},
},...config.plugins]};
