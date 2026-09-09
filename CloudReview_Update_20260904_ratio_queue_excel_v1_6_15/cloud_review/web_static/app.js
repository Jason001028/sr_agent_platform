const $ = (id) => document.getElementById(id);
const canvas = $("mapCanvas");
const ctx = canvas.getContext("2d");

const state = {
  scenes: [], filtered: [], selectedId: "", checked: new Set(),
  images: new Map(), loading: new Set(), failed: new Map(),
  assetQueue: [], queued: new Set(),
  centerX: 0, centerY: 0, scale: 100, fitted: false,
  pixelRatio: 0,
  dragging: false, moved: false, lastX: 0, lastY: 0,
  worldPolygons: [],
  ratioJobId: "", ratioCursor: 0, ratioTimer: 0,
  assetErrorMethods: new Set(),
  loadRevision: 0,
};
let pendingValidation = null;
const cloudTextStyle = {color:"#ef334e",size:16,showMethod:true};
try { const saved=JSON.parse(localStorage.getItem("cloudReviewTextStyle")||"{}");if(/^#[0-9a-f]{6}$/i.test(saved.color))cloudTextStyle.color=saved.color;if(Number.isFinite(saved.size))cloudTextStyle.size=Math.max(10,Math.min(36,saved.size));if(typeof saved.showMethod==="boolean")cloudTextStyle.showMethod=saved.showMethod; } catch {}
const cloudColorSettings = {low:10,high:80};
try { const saved=JSON.parse(localStorage.getItem("cloudReviewColorThresholds")||"{}");if(Number.isFinite(saved.low))cloudColorSettings.low=Math.max(0,Math.min(100,saved.low));if(Number.isFinite(saved.high))cloudColorSettings.high=Math.max(0,Math.min(100,saved.high));if(cloudColorSettings.low>cloudColorSettings.high)[cloudColorSettings.low,cloudColorSettings.high]=[cloudColorSettings.high,cloudColorSettings.low]; } catch {}

const methodNames = { combined: "综合", rdc: "RDC", omnicloudmask: "Omni", final: "最终" };
let toastTimer;
function toast(message, error=false) {
  const node = $("toast"); node.textContent = message;
  node.style.background = error ? "rgba(157,43,58,.94)" : "rgba(17,29,38,.92)";
  node.classList.add("show"); clearTimeout(toastTimer);
  toastTimer = setTimeout(() => node.classList.remove("show"), 2600);
}

async function api(path, payload) {
  const options = payload ? { method: "POST", headers: {"Content-Type":"application/json"}, body: JSON.stringify(payload) } : {};
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) { const error=new Error(data.error || "请求失败");error.status=response.status;error.path=path;throw error; }
  return data;
}
function showBusy(message) { $("loadingText").textContent=message; $("loading").classList.remove("hidden"); }
function hideBusy() { $("loading").classList.add("hidden"); }

function ratio(scene, method) {
  const value = scene.cloud_ratios?.[method];
  return Number.isFinite(value) ? value : null;
}
function percent(value) { return value == null ? "—" : `${(value*100).toFixed(1)}%`; }
function isAvailable(scene, method) { return scene.available_masks.includes(method); }
function escapeHtml(value) { return String(value??"").replace(/[&<>"']/g,char=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[char])); }
function scenePoints(scene) { return scene.corners || []; }
const MAX_MERCATOR_LAT=85.05112878,WORLD_MIN=-180,WORLD_MAX=180,WORLD_SIZE=360;
function mercatorLatitude(latitude){const lat=Math.max(-MAX_MERCATOR_LAT,Math.min(MAX_MERCATOR_LAT,latitude));return 180/Math.PI*Math.log(Math.tan(Math.PI/4+lat*Math.PI/360));}
function inverseMercatorLatitude(value){return 360/Math.PI*Math.atan(Math.exp(value*Math.PI/180))-90;}
function worldPoint(point){return [point[0],-mercatorLatitude(point[1])];}
function geoPoint(point){return [point[0],inverseMercatorLatitude(-point[1])];}
function project(point) { const world=worldPoint(point);return [(world[0]-state.centerX)*state.scale + canvas.width/2, (world[1]-state.centerY)*state.scale + canvas.height/2]; }
function screenWorld(x,y){return [(x-canvas.width/2)/state.scale+state.centerX,(y-canvas.height/2)/state.scale+state.centerY];}
function unproject(x,y) { return geoPoint(screenWorld(x,y)); }
function minimumWorldScale(){return canvas.width&&canvas.height?Math.min(canvas.width,canvas.height)/WORLD_SIZE*.94:.05;}
function clampView(){state.scale=Math.max(minimumWorldScale(),Math.min(state.scale,300000));const halfWidth=canvas.width/(2*state.scale),halfHeight=canvas.height/(2*state.scale);state.centerX=halfWidth>=WORLD_SIZE/2?0:Math.max(WORLD_MIN+halfWidth,Math.min(WORLD_MAX-halfWidth,state.centerX));state.centerY=halfHeight>=WORLD_SIZE/2?0:Math.max(WORLD_MIN+halfHeight,Math.min(WORLD_MAX-halfHeight,state.centerY));}
function resetWorldView(){state.centerX=0;state.centerY=0;state.scale=minimumWorldScale();clampView();}
function worldScreenRect(){return{left:(WORLD_MIN-state.centerX)*state.scale+canvas.width/2,right:(WORLD_MAX-state.centerX)*state.scale+canvas.width/2,top:(WORLD_MIN-state.centerY)*state.scale+canvas.height/2,bottom:(WORLD_MAX-state.centerY)*state.scale+canvas.height/2};}
function visibleScene(scene) {
  if (!scene.has_geometry) return false;
  const points = scenePoints(scene).map(project);
  const xs=points.map(p=>p[0]), ys=points.map(p=>p[1]);
  return Math.max(...xs)>=0 && Math.min(...xs)<=canvas.width && Math.max(...ys)>=0 && Math.min(...ys)<=canvas.height;
}

function resizeCanvas() {
  const rect=canvas.getBoundingClientRect(), dpr=Math.min(devicePixelRatio||1,2);
  const w=Math.round(rect.width*dpr), h=Math.round(rect.height*dpr);
  if (!state.pixelRatio) state.pixelRatio=dpr;
  if (canvas.width!==w || canvas.height!==h) { canvas.width=w; canvas.height=h; }
  if(!state.fitted&&!state.scenes.length)resetWorldView();else clampView();
  draw();
}

function fitScenes(scenes) {
  const points=scenes.filter(s=>s.has_geometry).flatMap(scenePoints);
  if (!points.length) return;
  const projected=points.map(worldPoint),xs=projected.map(p=>p[0]),ys=projected.map(p=>p[1]);
  const minX=Math.min(...xs), maxX=Math.max(...xs), minY=Math.min(...ys), maxY=Math.max(...ys);
  state.centerX=(minX+maxX)/2; state.centerY=(minY+maxY)/2;
  const pad=70*Math.min(devicePixelRatio||1,2);
  state.scale=Math.min((canvas.width-pad*2)/Math.max(maxX-minX,.01),(canvas.height-pad*2)/Math.max(maxY-minY,.01));
  state.fitted=true;clampView();draw();renderList();
}

function gridStep() {
  const candidates=[.001,.002,.005,.01,.02,.05,.1,.2,.5,1,2,5,10,20,50];
  return candidates.find(v=>v*state.scale>85) || 100;
}
function drawWorldLand() {
  if(!$("showBasemap").checked||!state.worldPolygons.length)return;
  ctx.fillStyle="#d8e2dc";ctx.strokeStyle="#a8b8b1";ctx.lineWidth=.7*Math.min(devicePixelRatio||1,2);
  state.worldPolygons.forEach(polygon=>{
    ctx.beginPath();
    polygon.forEach(ring=>{
      if(!ring.length)return;const first=project(ring[0]);ctx.moveTo(first[0],first[1]);
      for(let i=1;i<ring.length;i++){const point=project(ring[i]);ctx.lineTo(point[0],point[1]);}
      ctx.closePath();
    });
    ctx.fill("evenodd");ctx.stroke();
  });
}
function drawGrid() {
  ctx.fillStyle="#dfeaf0"; ctx.fillRect(0,0,canvas.width,canvas.height);drawWorldLand();
  const step=gridStep(), tl=unproject(0,0), br=unproject(canvas.width,canvas.height);
  const minLon=Math.max(-180,Math.min(tl[0],br[0])), maxLon=Math.min(180,Math.max(tl[0],br[0]));
  const minLat=Math.max(-MAX_MERCATOR_LAT,Math.min(tl[1],br[1])), maxLat=Math.min(MAX_MERCATOR_LAT,Math.max(tl[1],br[1]));
  ctx.strokeStyle="#d5dde2"; ctx.lineWidth=1; ctx.fillStyle="#8c9aa4"; ctx.font=`${10*Math.min(devicePixelRatio||1,2)}px sans-serif`;
  for(let lon=Math.floor(minLon/step)*step;lon<=maxLon;lon+=step){ const x=project([lon,0])[0];ctx.beginPath();ctx.moveTo(x,0);ctx.lineTo(x,canvas.height);ctx.stroke();ctx.fillText(`${lon.toFixed(step<1?2:0)}°`,x+4,14); }
  for(let lat=Math.floor(minLat/step)*step;lat<=maxLat;lat+=step){ const y=project([0,lat])[1];ctx.beginPath();ctx.moveTo(0,y);ctx.lineTo(canvas.width,y);ctx.stroke();ctx.fillText(`${lat.toFixed(step<1?2:0)}°`,4,y-4); }
}

function pathPolygon(points) { ctx.beginPath();ctx.moveTo(points[0][0],points[0][1]);for(let i=1;i<points.length;i++)ctx.lineTo(points[i][0],points[i][1]);ctx.closePath(); }
function heatColor(value, alpha) {
  const v=Math.max(0,Math.min(1,value??0));
  const stops=v<.5 ? [53+(246-53)*v*2,197+(198-197)*v*2,138+(77-138)*v*2] : [246+(230-246)*(v-.5)*2,198+(70-198)*(v-.5)*2,77+(93-77)*(v-.5)*2];
  return `rgba(${stops.map(Math.round).join(",")},${alpha})`;
}
function assetSize(scene) { return scene.scene_id===state.selectedId?768:256; }
function assetKey(scene,kind,size,color="") { return `${scene.scene_id}|${kind}|${size}|${color}`; }
function assetUrl(scene, kind, size, color="") { return `/api/asset?scene=${encodeURIComponent(scene.scene_id)}&kind=${encodeURIComponent(kind)}&size=${size}${color?`&color=${encodeURIComponent(color.replace("#",""))}`:""}&rev=${state.loadRevision}`; }
function requestImage(scene, kind, size, color="") {
  const key=assetKey(scene,kind,size,color);
  const failedAt=state.failed.get(key);if(failedAt&&Date.now()-failedAt<2500)return;if(failedAt)state.failed.delete(key);
  if(state.images.has(key)||state.loading.has(key)||state.queued.has(key)) return;
  const task={scene,kind,size,color,key};state.queued.add(key);kind==="image"?state.assetQueue.push(task):state.assetQueue.unshift(task);pumpAssets();
}
async function diagnoseAssetFailure(task){try{const suffix=`scene=${encodeURIComponent(task.scene.scene_id)}&kind=${encodeURIComponent(task.kind)}&size=${task.size}${task.color?`&color=${encodeURIComponent(task.color.replace("#",""))}`:""}`;const result=await api(`/api/asset-diagnostic?${suffix}`);if(result.ok){delete task.scene.asset_errors?.[task.kind];toast(`${methodNames[task.kind]}掩膜检查成功，正在重新显示`);state.failed.delete(task.key);draw();return;}task.scene.asset_errors=task.scene.asset_errors||{};task.scene.asset_errors[task.kind]=result.error||"未知错误";if(state.selectedId===task.scene.scene_id)renderDetail(task.scene);toast(`${methodNames[task.kind]}显示失败：${result.error||"未知错误"}`,true);}catch(error){toast(`${methodNames[task.kind]}诊断失败：${error.message}`,true);}}
function pumpAssets(){while(state.loading.size<2&&state.assetQueue.length){const task=state.assetQueue.shift();state.queued.delete(task.key);state.loading.add(task.key);const image=new Image();image.decoding="async";image.onload=()=>{state.loading.delete(task.key);state.failed.delete(task.key);if(task.scene.asset_errors)delete task.scene.asset_errors[task.kind];state.images.set(task.key,image);pumpAssets();draw();};image.onerror=()=>{state.loading.delete(task.key);state.failed.set(task.key,Date.now());if(task.kind!=="image"&&task.kind===$("maskLayer").value&&!state.assetErrorMethods.has(task.kind)){state.assetErrorMethods.add(task.kind);diagnoseAssetFailure(task);setTimeout(()=>{state.failed.delete(task.key);draw();},2600);}pumpAssets();};image.src=assetUrl(task.scene,task.kind,task.size,task.color);}}
function changeCloudMethod(){const method=$("maskLayer").value;state.assetQueue=state.assetQueue.filter(task=>task.kind==="image"||task.kind===method);state.queued=new Set(state.assetQueue.map(task=>task.key));state.failed.clear();state.assetErrorMethods.clear();draw();}
function maskColor(scene,method){const mode=$("maskColorMode").value;if(mode==="red")return "#ef334e";if(mode==="yellow")return "#f4c542";if(mode==="green")return "#27ae60";const ratioValue=ratio(scene,method);if(ratioValue==null)return "#ef334e";const value=ratioValue*100;return value<cloudColorSettings.low?"#27ae60":value<=cloudColorSettings.high?"#f4c542":"#ef334e";}
function bilinearGeo(corners,u,v){const [ul,ur,lr,ll]=corners,w00=(1-u)*(1-v),w10=u*(1-v),w11=u*v,w01=(1-u)*v;return [ul[0]*w00+ur[0]*w10+lr[0]*w11+ll[0]*w01,ul[1]*w00+ur[1]*w10+lr[1]*w11+ll[1]*w01];}
function createWarpRenderer(){const surface=document.createElement("canvas"),gl=surface.getContext("webgl",{alpha:true,premultipliedAlpha:true,antialias:false});if(!gl)return null;const compile=(type,source)=>{const shader=gl.createShader(type);gl.shaderSource(shader,source);gl.compileShader(shader);return gl.getShaderParameter(shader,gl.COMPILE_STATUS)?shader:null;},vertex=compile(gl.VERTEX_SHADER,"attribute vec2 a_position;attribute vec2 a_texcoord;varying vec2 v_texcoord;void main(){gl_Position=vec4(a_position,0.0,1.0);v_texcoord=a_texcoord;}"),fragment=compile(gl.FRAGMENT_SHADER,"precision mediump float;uniform sampler2D u_image;varying vec2 v_texcoord;void main(){gl_FragColor=texture2D(u_image,v_texcoord);}");if(!vertex||!fragment)return null;const program=gl.createProgram();gl.attachShader(program,vertex);gl.attachShader(program,fragment);gl.linkProgram(program);if(!gl.getProgramParameter(program,gl.LINK_STATUS))return null;const buffer=gl.createBuffer(),textures=new WeakMap();return{surface,gl,program,buffer,textures,position:gl.getAttribLocation(program,"a_position"),texcoord:gl.getAttribLocation(program,"a_texcoord")};}
const warpRenderer=createWarpRenderer();
function drawWarpedWebGL(image,corners,opacity,meshSize){if(!warpRenderer||!image||corners.length<4)return false;const {surface,gl,program,buffer,textures}=warpRenderer;if(surface.width!==canvas.width||surface.height!==canvas.height){surface.width=canvas.width;surface.height=canvas.height;}gl.viewport(0,0,surface.width,surface.height);gl.clearColor(0,0,0,0);gl.clear(gl.COLOR_BUFFER_BIT);const columns=Math.max(2,meshSize),rows=Math.max(2,meshSize),points=Array.from({length:rows+1},(_,row)=>Array.from({length:columns+1},(_,column)=>project(bilinearGeo(corners,column/columns,row/rows)))),vertices=[],push=(point,u,v)=>vertices.push(point[0]/surface.width*2-1,1-point[1]/surface.height*2,u,v);for(let row=0;row<rows;row++)for(let column=0;column<columns;column++){const u0=column/columns,u1=(column+1)/columns,v0=row/rows,v1=(row+1)/rows,p00=points[row][column],p10=points[row][column+1],p11=points[row+1][column+1],p01=points[row+1][column];push(p00,u0,v0);push(p10,u1,v0);push(p11,u1,v1);push(p00,u0,v0);push(p11,u1,v1);push(p01,u0,v1);}gl.useProgram(program);gl.bindBuffer(gl.ARRAY_BUFFER,buffer);gl.bufferData(gl.ARRAY_BUFFER,new Float32Array(vertices),gl.DYNAMIC_DRAW);gl.enableVertexAttribArray(warpRenderer.position);gl.vertexAttribPointer(warpRenderer.position,2,gl.FLOAT,false,16,0);gl.enableVertexAttribArray(warpRenderer.texcoord);gl.vertexAttribPointer(warpRenderer.texcoord,2,gl.FLOAT,false,16,8);let texture=textures.get(image);if(!texture){texture=gl.createTexture();gl.bindTexture(gl.TEXTURE_2D,texture);gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL,false);gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL,true);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_S,gl.CLAMP_TO_EDGE);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_T,gl.CLAMP_TO_EDGE);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MIN_FILTER,gl.LINEAR);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MAG_FILTER,gl.LINEAR);gl.texImage2D(gl.TEXTURE_2D,0,gl.RGBA,gl.RGBA,gl.UNSIGNED_BYTE,image);textures.set(image,texture);}else gl.bindTexture(gl.TEXTURE_2D,texture);gl.disable(gl.BLEND);gl.drawArrays(gl.TRIANGLES,0,vertices.length/4);ctx.save();ctx.globalAlpha=opacity;ctx.drawImage(surface,0,0);ctx.restore();return true;}
function expandedTriangle(points,pixels=2){const center=[points.reduce((sum,p)=>sum+p[0],0)/3,points.reduce((sum,p)=>sum+p[1],0)/3];return points.map(point=>{const dx=point[0]-center[0],dy=point[1]-center[1],length=Math.hypot(dx,dy)||1,scale=(length+pixels)/length;return[center[0]+dx*scale,center[1]+dy*scale];});}
function drawMeshTriangle(targetContext,image,source,destination){const [s0,s1,s2]=source,[d0,d1,d2]=destination,det=(s0[0]*(s1[1]-s2[1])+s1[0]*(s2[1]-s0[1])+s2[0]*(s0[1]-s1[1]));if(Math.abs(det)<1e-9)return;const a=(d0[0]*(s1[1]-s2[1])+d1[0]*(s2[1]-s0[1])+d2[0]*(s0[1]-s1[1]))/det,b=(d0[1]*(s1[1]-s2[1])+d1[1]*(s2[1]-s0[1])+d2[1]*(s0[1]-s1[1]))/det,c=(d0[0]*(s2[0]-s1[0])+d1[0]*(s0[0]-s2[0])+d2[0]*(s1[0]-s0[0]))/det,d=(d0[1]*(s2[0]-s1[0])+d1[1]*(s0[0]-s2[0])+d2[1]*(s1[0]-s0[0]))/det,e=(d0[0]*(s1[0]*s2[1]-s2[0]*s1[1])+d1[0]*(s2[0]*s0[1]-s0[0]*s2[1])+d2[0]*(s0[0]*s1[1]-s1[0]*s0[1]))/det,f=(d0[1]*(s1[0]*s2[1]-s2[0]*s1[1])+d1[1]*(s2[0]*s0[1]-s0[0]*s2[1])+d2[1]*(s0[0]*s1[1]-s1[0]*s0[1]))/det,clip=expandedTriangle(destination);targetContext.save();targetContext.beginPath();targetContext.moveTo(clip[0][0],clip[0][1]);targetContext.lineTo(clip[1][0],clip[1][1]);targetContext.lineTo(clip[2][0],clip[2][1]);targetContext.closePath();targetContext.clip();targetContext.globalCompositeOperation="copy";targetContext.imageSmoothingEnabled=true;targetContext.setTransform(a,b,c,d,e,f);targetContext.drawImage(image,0,0);targetContext.restore();}
function drawWarped(image,corners,opacity,meshSize=8) {
  if(!image||corners.length<4)return;
  if(drawWarpedWebGL(image,corners,opacity,meshSize))return;
  const width=image.naturalWidth||image.width,height=image.naturalHeight||image.height,columns=Math.max(2,meshSize),rows=Math.max(2,meshSize);
  const screenPoints=Array.from({length:rows+1},(_,row)=>Array.from({length:columns+1},(_,column)=>project(bilinearGeo(corners,column/columns,row/rows)))),flat=screenPoints.flat(),minX=Math.floor(Math.min(...flat.map(point=>point[0])))-2,minY=Math.floor(Math.min(...flat.map(point=>point[1])))-2,maxX=Math.ceil(Math.max(...flat.map(point=>point[0])))+2,maxY=Math.ceil(Math.max(...flat.map(point=>point[1])))+2,layerWidth=maxX-minX,layerHeight=maxY-minY;if(layerWidth<1||layerHeight<1||layerWidth>8192||layerHeight>8192)return;
  const layer=document.createElement("canvas");layer.width=layerWidth;layer.height=layerHeight;const layerContext=layer.getContext("2d"),points=screenPoints.map(row=>row.map(point=>[point[0]-minX,point[1]-minY])),perimeter=[...points[0],...points.slice(1).map(row=>row[columns]),...points[rows].slice(0,-1).reverse(),...points.slice(1,-1).reverse().map(row=>row[0])];layerContext.beginPath();layerContext.moveTo(perimeter[0][0],perimeter[0][1]);perimeter.slice(1).forEach(point=>layerContext.lineTo(point[0],point[1]));layerContext.closePath();layerContext.clip();
  for(let row=0;row<rows;row++)for(let column=0;column<columns;column++){const x0=width*column/columns,x1=width*(column+1)/columns,y0=height*row/rows,y1=height*(row+1)/rows,p00=points[row][column],p10=points[row][column+1],p11=points[row+1][column+1],p01=points[row+1][column];drawMeshTriangle(layerContext,image,[[x0,y0],[x1,y0],[x1,y1]],[p00,p10,p11]);drawMeshTriangle(layerContext,image,[[x0,y0],[x1,y1],[x0,y1]],[p00,p11,p01]);}
  ctx.save();ctx.globalAlpha=opacity;ctx.drawImage(layer,minX,minY);ctx.restore();
}

function sceneBoundaryPoints(scene,segments=8){const corners=scenePoints(scene);if(corners.length<4)return[];const points=[];for(let edge=0;edge<4;edge++){const start=corners[edge],end=corners[(edge+1)%4];for(let index=0;index<segments;index++){const t=index/segments;points.push([start[0]+(end[0]-start[0])*t,start[1]+(end[1]-start[1])*t]);}}return points;}

function scenesToDraw(){ const checked=state.checked.size?state.scenes.filter(s=>state.checked.has(s.scene_id)):state.scenes; return checked.filter(visibleScene); }
function draw() {
  if(!canvas.width)return;ctx.clearRect(0,0,canvas.width,canvas.height);ctx.fillStyle="#c8d2d9";ctx.fillRect(0,0,canvas.width,canvas.height);const worldRect=worldScreenRect();ctx.save();ctx.beginPath();ctx.rect(worldRect.left,worldRect.top,worldRect.right-worldRect.left,worldRect.bottom-worldRect.top);ctx.clip();drawGrid();
  const showImage=$("showImage").checked, showBoundary=$("showBoundary").checked, showLabel=$("showLabel").checked;
  const selectedMethod=$("maskLayer").value,mask=$("showMask").checked?selectedMethod:"none",cloudText=$("showCloudText").checked?selectedMethod:"none",opacity=Number($("layerOpacity").value)/100;
  const visible=scenesToDraw();
  visible.forEach(scene=>{
    const points=scenePoints(scene).map(project);
    const size=assetSize(scene),fallbackSize=256;
    const meshSize=scene.scene_id===state.selectedId?16:8;
    if(showImage && scene.has_image){const key=assetKey(scene,"image",size),fallback=assetKey(scene,"image",fallbackSize);drawWarped(state.images.get(key)||state.images.get(fallback),scenePoints(scene),.9,meshSize);requestImage(scene,"image",size);}
    if(mask!=="none" && isAvailable(scene,mask)){const color=maskColor(scene,mask),key=assetKey(scene,mask,size,color),fallback=assetKey(scene,mask,fallbackSize,color);drawWarped(state.images.get(key)||state.images.get(fallback),scenePoints(scene),opacity,meshSize);requestImage(scene,mask,size,color);}
    if(showBoundary || scene.scene_id===state.selectedId){pathPolygon(sceneBoundaryPoints(scene,meshSize).map(project));ctx.strokeStyle=scene.scene_id===state.selectedId?"#ffe052":"#16859a";ctx.lineWidth=(scene.scene_id===state.selectedId?3:1.2)*Math.min(devicePixelRatio||1,2);ctx.stroke();}
    if(scene.scene_id===state.selectedId){const names=["左上","右上","右下","左下"];points.forEach((point,index)=>{ctx.save();ctx.beginPath();ctx.arc(point[0],point[1],4*Math.min(devicePixelRatio||1,2),0,Math.PI*2);ctx.fillStyle="#ffe052";ctx.fill();ctx.strokeStyle="#18313b";ctx.lineWidth=1.2*Math.min(devicePixelRatio||1,2);ctx.stroke();ctx.font=`700 ${10*Math.min(devicePixelRatio||1,2)}px "Microsoft YaHei UI",sans-serif`;ctx.fillStyle="#18313b";ctx.strokeStyle="rgba(255,255,255,.95)";ctx.lineWidth=3*Math.min(devicePixelRatio||1,2);ctx.strokeText(names[index],point[0]+7,point[1]-7);ctx.fillText(names[index],point[0]+7,point[1]-7);ctx.restore();});}
    const cloudValue=cloudText==="none"?null:ratio(scene,cloudText);
    if(cloudValue!=null){const center=points.reduce((sum,point)=>[sum[0]+point[0]/points.length,sum[1]+point[1]/points.length],[0,0]);const percentText=`${(cloudValue*100).toFixed(1)}%`,text=cloudTextStyle.showMethod?`${methodNames[cloudText]} ${percentText}`:percentText;const fontSize=cloudTextStyle.size*(state.pixelRatio||1);ctx.save();ctx.font=`700 ${fontSize}px "Microsoft YaHei UI",sans-serif`;ctx.textAlign="center";ctx.textBaseline="middle";ctx.lineJoin="round";ctx.strokeStyle="rgba(255,255,255,.92)";ctx.lineWidth=Math.max(2,fontSize*.22);ctx.strokeText(text,center[0],center[1]);ctx.fillStyle=cloudTextStyle.color;ctx.fillText(text,center[0],center[1]);ctx.restore();}
    if(showLabel){const p=points[0];ctx.font=`${10*Math.min(devicePixelRatio||1,2)}px sans-serif`;ctx.fillStyle="#14232c";ctx.fillText(scene.scene_id,p[0]+4,p[1]-5);}
  });
  ctx.restore();ctx.save();ctx.strokeStyle="#71818d";ctx.lineWidth=Math.max(1,state.pixelRatio||1);ctx.strokeRect(worldRect.left,worldRect.top,worldRect.right-worldRect.left,worldRect.bottom-worldRect.top);ctx.restore();
  $("legend").classList.toggle("hidden",mask==="none"||$("maskColorMode").value!=="auto");
}

function pointInPolygon(x,y,points){let inside=false;for(let i=0,j=points.length-1;i<points.length;j=i++){const xi=points[i][0],yi=points[i][1],xj=points[j][0],yj=points[j][1];const hit=((yi>y)!==(yj>y))&&(x<(xj-xi)*(y-yi)/(yj-yi)+xi);if(hit)inside=!inside;}return inside;}
function pickAt(x,y){const hits=scenesToDraw().filter(s=>pointInPolygon(x,y,sceneBoundaryPoints(s,8).map(project)));if(hits.length)selectScene(hits[hits.length-1].scene_id);}

function applyFilter() {
  const q=$("keyword").value.trim().toLowerCase(), sort=$("sortMode").value;
  state.filtered=state.scenes.filter(s=>!q||`${s.scene_id} ${s.satellite} ${s.plan_code}`.toLowerCase().includes(q));
  if($("onlyVisible").checked)state.filtered=state.filtered.filter(visibleScene);
  state.filtered.sort((a,b)=>{
    if(sort==="date")return `${b.acquired_date}${b.scene_id}`.localeCompare(`${a.acquired_date}${a.scene_id}`);
    if(sort==="difference")return (b.difference||0)-(a.difference||0);
    if(sort.startsWith("order_")){const methods=sort.slice(6).split("_");for(const method of methods){const delta=(ratio(b,method)??-1)-(ratio(a,method)??-1);if(delta)return delta;}return a.scene_id.localeCompare(b.scene_id);}
    return (ratio(b,sort)??-1)-(ratio(a,sort)??-1);
  }); renderList();
}
function ratioBox(scene,method,label){const v=ratio(scene,method),available=isAvailable(scene,method);let text=percent(v);if(available&&v==null)text=scene.ratio_errors?.[method]?"失败":scene.ratio_state==="pending"?"计算中":"—";return `<span class="ratio ${!available?"missing":""}"${scene.ratio_errors?.[method]?` title="${escapeHtml(scene.ratio_errors[method])}"`:""}>${label}<b>${text}</b></span>`;}
function updateSelectAllState(){const selectAll=$("selectAllScenes"),selectedCount=state.filtered.filter(scene=>state.checked.has(scene.scene_id)).length;selectAll.disabled=!state.filtered.length;selectAll.checked=state.filtered.length>0&&selectedCount===state.filtered.length;selectAll.indeterminate=selectedCount>0&&selectedCount<state.filtered.length;}
function renderList() {
  const list=$("sceneList");
  if(!state.filtered.length){list.innerHTML='<div class="empty-list">当前没有加载任何影像。<br>点击上方 ↻ 加载待复核队列，<br>或展开“组合检索”按需加载。</div>';}
  else list.innerHTML=state.filtered.map(scene=>`<article class="scene-card ${scene.scene_id===state.selectedId?"active":""}" data-id="${scene.scene_id}">
    <input type="checkbox" ${state.checked.has(scene.scene_id)?"checked":""} aria-label="勾选本景">
    <div><div class="scene-title" title="${scene.scene_id}">${scene.scene_id}</div><div class="scene-meta"><span><i class="geo-dot ${scene.has_geometry?"":"missing"}"></i>${scene.satellite}</span><span>${scene.acquired_date} · ${scene.status}</span></div>
    <div class="ratios">${ratioBox(scene,"combined","综合")}${ratioBox(scene,"rdc","RDC")}${ratioBox(scene,"omnicloudmask","Omni")}</div></div></article>`).join("");
  list.querySelectorAll(".scene-card").forEach(card=>{
    card.addEventListener("click",e=>{if(e.target.tagName!=="INPUT")selectScene(card.dataset.id);});
    card.querySelector("input").addEventListener("change",e=>{e.target.checked?state.checked.add(card.dataset.id):state.checked.delete(card.dataset.id);updateSelectAllState();draw();});
  });
  $("sceneCount").textContent=`${state.filtered.length} 景`;
  $("geoCount").textContent=`${state.scenes.filter(s=>s.has_geometry).length} 景有空间信息`;
  updateSelectAllState();
}
function revealSceneCard(id) {
  const list=$("sceneList"),card=list.querySelector(`.scene-card[data-id="${CSS.escape(id)}"]`);
  if(!card)return;
  const listRect=list.getBoundingClientRect(),cardRect=card.getBoundingClientRect();
  if(cardRect.top<listRect.top)list.scrollTop-=listRect.top-cardRect.top;
  else if(cardRect.bottom>listRect.bottom)list.scrollTop+=cardRect.bottom-listRect.bottom;
}
function selectScene(id) {
  state.selectedId=id; const scene=state.scenes.find(s=>s.scene_id===id); if(!scene)return;
  renderList(); renderDetail(scene); draw();
  revealSceneCard(id);
}
function renderDetail(scene) {
  const buttons=["combined","rdc","omnicloudmask","final"].filter(m=>isAvailable(scene,m)).map(m=>`<button data-method="${m}">采用${methodNames[m]}</button>`).join("");
  const geoPath=scene.geometry_source||"",geoFile=geoPath.split(/[\\/]/).pop();const geoSource=geoPath.toLowerCase().endsWith(".xml")?`meta.xml四角坐标（${geoFile}）`:geoPath.toLowerCase().endsWith(".shp")?`SHP真实多边形顶点（XML缺失/解析失败，${geoFile}）`:"未知来源";const rotation=Number.isFinite(scene.geometry_rotation_degrees)?` · 顶边倾角 ${scene.geometry_rotation_degrees.toFixed(1)}°`:"";const geoShape=scene.geometry_shape==="rotated"?`倾斜四边形${rotation}`:scene.geometry_shape==="axis_aligned"?"轴向矩形":"未知形状";const cornerText=(scene.corners||[]).map((p,i)=>`${["左上","右上","右下","左下"][i]} ${p[0].toFixed(6)},${p[1].toFixed(6)}`).join(" ｜ ");
  const maskPathRows=["combined","rdc","omnicloudmask","final"].map(method=>{const path=scene.mask_paths?.[method]||"",error=scene.asset_errors?.[method]||"";return `<div class="mask-path ${path?"":"missing"}"><b>${methodNames[method]}：</b><span title="${escapeHtml(path)}">${path?escapeHtml(path):"未找到"}${error?`<em>显示错误：${escapeHtml(error)}</em>`:""}</span></div>`;}).join("");
  $("detailBar").innerHTML=`<div class="detail-content"><div><div class="detail-title">${escapeHtml(scene.scene_id)}</div><div class="detail-sub">${escapeHtml(scene.satellite)} · ${escapeHtml(scene.acquired_date)} · 综合 ${percent(ratio(scene,"combined"))} · RDC ${percent(ratio(scene,"rdc"))} · Omni ${percent(ratio(scene,"omnicloudmask"))} · 最终 ${percent(ratio(scene,"final"))} · 差异 ${(scene.difference*100).toFixed(1)}%</div><div class="detail-sub" title="${escapeHtml(cornerText)}">定位：${escapeHtml(geoSource)} · ${escapeHtml(geoShape)}（悬停查看四角坐标）</div><div class="detail-sub geometry-diagnostic">${escapeHtml(scene.geometry_diagnostic||"")}</div><div class="mask-paths"><div class="mask-path-heading">掩膜实际路径</div>${maskPathRows}</div></div><div class="detail-actions">${buttons}<button class="skip">跳过</button></div></div>`;
  $("detailBar").querySelectorAll("[data-method]").forEach(button=>button.onclick=()=>decision(scene,"accept",button.dataset.method));
  $("detailBar").querySelector(".skip").onclick=()=>decision(scene,"skip","");
}
async function decision(scene,action,method) {
  try { const result=await api("/api/decision",{scene_id:scene.scene_id,decision:action,method,label_type:"cloud",note:"Web空间复核"}); scene.status=action==="skip"?"skipped":"staged";if(result.available_masks)scene.available_masks=result.available_masks;if(result.mask_paths)scene.mask_paths=result.mask_paths;if(result.cloud_ratios)scene.cloud_ratios=result.cloud_ratios; toast(action==="skip"?"已标记跳过，原始文件均保留":`已采用${methodNames[method]}，加入待入库`); applyFilter(); renderDetail(scene);draw(); }
  catch(error){toast(error.message,true);}
}

function stopRatioPolling(){if(state.ratioTimer)clearTimeout(state.ratioTimer);state.ratioTimer=0;state.ratioJobId="";state.ratioCursor=0;}
function updateRatioScenes(updates){updates.forEach(update=>{const scene=state.scenes.find(item=>item.scene_id===update.scene_id);if(!scene)return;scene.cloud_ratios=update.cloud_ratios||{};scene.difference=update.difference||0;scene.difference_percent=update.difference_percent||0;scene.iou=update.iou??1;scene.ratio_state=update.ratio_state||"complete";scene.ratio_errors=update.ratio_errors||{};});renderList();const selected=state.scenes.find(item=>item.scene_id===state.selectedId);if(selected)renderDetail(selected);draw();}
async function pollRatioProgress(){if(!state.ratioJobId)return;const jobId=state.ratioJobId;try{const data=await api(`/api/ratio-progress?job=${encodeURIComponent(jobId)}&cursor=${state.ratioCursor}`);if(jobId!==state.ratioJobId)return;state.ratioCursor=data.cursor||0;if(data.updates?.length)updateRatioScenes(data.updates);if(data.done){(data.errors||[]).forEach(item=>{const scene=state.scenes.find(value=>value.scene_id===item.scene_id);if(scene)scene.ratio_state="error";});state.ratioJobId="";applyFilter();const errorCount=data.errors?.length||0;toast(`云量计算完成：${data.completed}/${data.total}${errorCount?`，失败 ${errorCount} 景`:""}`,errorCount>0);return;}state.ratioTimer=setTimeout(pollRatioProgress,500);}catch(error){if(jobId===state.ratioJobId)state.ratioTimer=setTimeout(pollRatioProgress,1200);}}
function applyLoadedData(data,label) {
  stopRatioPolling();state.loadRevision+=1;state.scenes=data.scenes;state.filtered=[...state.scenes];state.checked.clear();state.selectedId="";state.images.clear();state.failed.clear();state.assetErrorMethods.clear();state.assetQueue=[];state.queued.clear();applyFilter();fitScenes(state.scenes);$("detailBar").innerHTML='<div class="empty-detail">从左侧列表或地图中选择一景</div>';toast(`${label}：加载 ${data.count} 景${data.missing?.length?`，未找到 ${data.missing.length} 景`:""}`);if(data.ratio_job_id){state.ratioJobId=data.ratio_job_id;state.ratioCursor=0;state.ratioTimer=setTimeout(pollRatioProgress,250);}
}

async function loadScenes(payload,label) {
  try { showBusy("正在按固定路径检索场景…"); const data=await api("/api/search",payload); applyLoadedData(data,label); }
  catch(error){toast(error.message,true);} finally{hideBusy();}
}

async function validateThenAsk(payload,label) {
  try {
    showBusy("正在检索匹配数量，尚未加载影像…");
    const backend=await api("/api/config");
    if(!backend.capabilities?.includes("precheck"))throw new Error("Web后台仍是旧版本。请关闭原来的Web启动窗口，重新运行 run_cloud_review_web.bat 后再试。");
    const data=await api("/api/validate",payload);
    pendingValidation={token:data.token,label};
    const missing=data.missing?.length||0;
    $("validationMessage").textContent=`共找到 ${data.count} 景${missing?`，另有 ${missing} 个ID未找到`:""}。是否加载到地图？`;
    $("confirmValidation").disabled=data.count===0;
    $("validationModal").classList.remove("hidden");
  } catch(error) { toast(error.status===404?"Web后台仍是旧版本。请关闭原来的Web启动窗口并重新启动Web端。":error.message,true); }
  finally { hideBusy(); }
}

async function loadValidated() {
  if(!pendingValidation)return;
  const prepared=pendingValidation;pendingValidation=null;$("validationModal").classList.add("hidden");
  try { showBusy("正在读取meta并准备场景列表…"); const data=await api("/api/load-validation",{token:prepared.token});applyLoadedData(data,prepared.label); }
  catch(error){toast(error.message,true);}finally{hideBusy();}
}

function requestedLoad(payload,label){const directScene=/_L1_MSS[\\/]?$/i.test(String(payload.path||"").trim());if(payload.mode==="search"&&!directScene&&(!String(payload.date||"").trim()||!String(payload.plan||"").trim())){toast("组合检索必须同时填写日期和计划号",true);return;}$("validateBeforeLoad").checked?validateThenAsk(payload,label):loadScenes(payload,label);}

canvas.addEventListener("mousedown",e=>{const r=canvas.getBoundingClientRect();state.dragging=true;state.moved=false;state.lastX=e.offsetX*canvas.width/r.width;state.lastY=e.offsetY*canvas.height/r.height;canvas.classList.add("dragging");});
window.addEventListener("mousemove",e=>{if(!state.dragging)return;const r=canvas.getBoundingClientRect(),x=(e.clientX-r.left)*canvas.width/r.width,y=(e.clientY-r.top)*canvas.height/r.height;const dx=x-state.lastX,dy=y-state.lastY;if(Math.abs(dx)+Math.abs(dy)>2)state.moved=true;state.centerX-=dx/state.scale;state.centerY-=dy/state.scale;clampView();state.lastX=x;state.lastY=y;draw();});
window.addEventListener("mouseup",()=>{state.dragging=false;canvas.classList.remove("dragging");if($("onlyVisible").checked)applyFilter();});
canvas.addEventListener("click",e=>{if(!state.moved){const r=canvas.getBoundingClientRect();pickAt(e.offsetX*canvas.width/r.width,e.offsetY*canvas.height/r.height);}});
canvas.addEventListener("wheel",e=>{e.preventDefault();const r=canvas.getBoundingClientRect(),x=e.offsetX*canvas.width/r.width,y=e.offsetY*canvas.height/r.height,before=screenWorld(x,y),factor=e.deltaY<0?1.22:1/1.22;state.scale*=factor;clampView();const after=screenWorld(x,y);state.centerX+=before[0]-after[0];state.centerY+=before[1]-after[1];clampView();draw();if($("onlyVisible").checked)applyFilter();},{passive:false});

function zoom(factor){state.scale*=factor;clampView();draw();}
$("zoomIn").onclick=()=>zoom(1.3);$("zoomOut").onclick=()=>zoom(1/1.3);
$("fitAll").onclick=()=>state.scenes.some(hasGeometry)?fitScenes(state.scenes):(resetWorldView(),draw());
$("fitSelected").onclick=()=>{const s=state.scenes.find(v=>v.scene_id===state.selectedId);if(s)fitScenes([s]);};
$("clearChecks").onclick=()=>{state.checked.clear();renderList();draw();};
$("selectAllScenes").addEventListener("change",event=>{state.filtered.forEach(scene=>event.target.checked?state.checked.add(scene.scene_id):state.checked.delete(scene.scene_id));renderList();draw();});
function syncDisplayControls(){const maskEnabled=$("showMask").checked,textEnabled=$("showCloudText").checked;$("maskLayer").disabled=!(maskEnabled||textEnabled);$("maskColorMode").disabled=!maskEnabled;$("cloudColorSettings").disabled=!maskEnabled;$("cloudTextSettings").disabled=!textEnabled;draw();}
["showBasemap","showImage","showBoundary","showLabel","maskColorMode"].forEach(id=>$(id).addEventListener("change",draw));$("maskLayer").addEventListener("change",changeCloudMethod);
$("showMask").addEventListener("change",syncDisplayControls);$("showCloudText").addEventListener("change",syncDisplayControls);syncDisplayControls();
$("layerOpacity").oninput=()=>{$("opacityValue").textContent=`${$("layerOpacity").value}%`;draw();};
$("keyword").oninput=applyFilter;$("sortMode").onchange=applyFilter;$("onlyVisible").onchange=applyFilter;
$("refreshQueue").onclick=()=>loadScenes({mode:"queue",limit:1000},"待复核队列");
$("searchPath").onclick=()=>requestedLoad({mode:"search",path:$("rootPath").value,date:$("dateFilter").value,plan:$("planFilter").value,limit:1000},"组合检索");
$("searchIds").onclick=()=>requestedLoad({mode:"ids",path:$("rootPath").value,ids:$("idFilter").value,limit:1000},"指定ID检索");
$("cancelValidation").onclick=()=>{pendingValidation=null;$("validationModal").classList.add("hidden");toast("已取消加载");};
$("confirmValidation").onclick=loadValidated;
function updateTextSettings(){cloudTextStyle.color=$("cloudTextColor").value;cloudTextStyle.size=Number($("cloudTextSize").value);cloudTextStyle.showMethod=$("cloudTextShowMethod").checked;$("cloudTextSizeValue").textContent=`${cloudTextStyle.size} px`;$("cloudTextPreview").style.color=cloudTextStyle.color;$("cloudTextPreview").style.fontSize=`${cloudTextStyle.size}px`;$("cloudTextPreview").textContent=cloudTextStyle.showMethod?"综合 12.3%":"12.3%";try{localStorage.setItem("cloudReviewTextStyle",JSON.stringify(cloudTextStyle));}catch{}draw();}
$("cloudTextColor").value=cloudTextStyle.color;$("cloudTextSize").value=String(cloudTextStyle.size);$("cloudTextShowMethod").checked=cloudTextStyle.showMethod;updateTextSettings();
$("cloudTextSettings").onclick=()=>$("textSettingsModal").classList.remove("hidden");
$("closeTextSettings").onclick=()=>$("textSettingsModal").classList.add("hidden");
$("cloudTextColor").oninput=updateTextSettings;$("cloudTextSize").oninput=updateTextSettings;$("cloudTextShowMethod").onchange=updateTextSettings;
$("resetTextSettings").onclick=()=>{$("cloudTextColor").value="#ef334e";$("cloudTextSize").value="16";$("cloudTextShowMethod").checked=true;updateTextSettings();};
function updateCloudThresholds(){let low=Math.max(0,Math.min(100,Number($("cloudLowThreshold").value)||0)),high=Math.max(0,Math.min(100,Number($("cloudHighThreshold").value)||0));if(low>high)[low,high]=[high,low];cloudColorSettings.low=low;cloudColorSettings.high=high;$("cloudLowThreshold").value=String(low);$("cloudHighThreshold").value=String(high);$("legendLow").textContent=`<${low}% 绿`;$("legendHigh").textContent=`>${high}% 红`;try{localStorage.setItem("cloudReviewColorThresholds",JSON.stringify(cloudColorSettings));}catch{}draw();}
$("cloudLowThreshold").value=String(cloudColorSettings.low);$("cloudHighThreshold").value=String(cloudColorSettings.high);updateCloudThresholds();
$("cloudColorSettings").onclick=()=>$("cloudColorModal").classList.remove("hidden");
$("closeCloudColorSettings").onclick=()=>{updateCloudThresholds();$("cloudColorModal").classList.add("hidden");};
$("cloudLowThreshold").onchange=updateCloudThresholds;$("cloudHighThreshold").onchange=updateCloudThresholds;
$("resetCloudThresholds").onclick=()=>{$("cloudLowThreshold").value="10";$("cloudHighThreshold").value="80";updateCloudThresholds();};
function renderCloudStats(){const low=cloudColorSettings.low,high=cloudColorSettings.high,total=state.filtered.length;$("cloudStatsScope").textContent=`当前筛选 ${total} 景：低云量 < ${low}%，中云量 ${low}%–${high}%，高云量 > ${high}%`;$("cloudStatsBody").innerHTML=["combined","rdc","omnicloudmask","final"].map(method=>{let lowCount=0,middleCount=0,highCount=0,valid=0;state.filtered.forEach(scene=>{const value=ratio(scene,method);if(value==null)return;valid+=1;const percentValue=value*100;if(percentValue<low)lowCount+=1;else if(percentValue>high)highCount+=1;else middleCount+=1;});return `<tr><th>${methodNames[method]}</th><td class="low">${lowCount}</td><td class="middle">${middleCount}</td><td class="high">${highCount}</td><td>${total-valid}</td></tr>`;}).join("");}
$("cloudStats").onclick=()=>{renderCloudStats();$("cloudStatsModal").classList.remove("hidden");};
$("closeCloudStats").onclick=()=>$("cloudStatsModal").classList.add("hidden");
$("exportCloudStats").onclick=async()=>{const button=$("exportCloudStats");try{button.disabled=true;button.textContent="正在生成…";const data=await api("/api/export-cloud-stats",{scene_ids:state.filtered.map(scene=>scene.scene_id)});const link=document.createElement("a");link.href=`/api/download-cloud-stats?token=${encodeURIComponent(data.token)}`;link.download=data.filename||"cloud_review_statistics.xlsx";document.body.appendChild(link);link.click();link.remove();toast(`已生成 ${state.filtered.length} 景云量统计Excel`);}catch(error){toast(error.message,true);}finally{button.disabled=false;button.textContent="导出Excel";}};
window.addEventListener("resize",resizeCanvas);

resizeCanvas();
renderList();
fetch("/world_land_110m.geojson").then(response=>response.json()).then(data=>{
  state.worldPolygons=data.features.flatMap(feature=>feature.geometry.type==="Polygon"?[feature.geometry.coordinates]:feature.geometry.type==="MultiPolygon"?feature.geometry.coordinates:[]);
  if(!state.scenes.length&&!state.fitted)resetWorldView();
  draw();
}).catch(()=>{});
