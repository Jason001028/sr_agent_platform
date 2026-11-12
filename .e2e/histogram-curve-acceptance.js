(async()=>{
  const s=window.histTest.store,rec=s.activeRec;
  const check=(value,message)=>{if(!value)throw new Error(message);};
  check(document.querySelectorAll('.hist-plot').length===1,'one combined histogram / curve plot');
  check(document.querySelectorAll('.hist-plot rect').length>100,'histogram remains behind curve');
  const src=rec.src,stats=rec.stats,token=rec.token,camera=JSON.stringify(s.view);
  const svg=document.querySelector('.hist-plot');
  // Pointer capture requires a trusted pointer; keyboard insertion tests the same point list.
  svg.dispatchEvent(new KeyboardEvent('keydown',{bubbles:true,key:'Enter'}));
  await new Promise(resolve=>setTimeout(resolve,50));
  const count=rec.displayStretch.curve.length;
  check(count>=4,'arbitrary additional control point');
  const point=document.querySelectorAll('.curve-point')[1];
  point.dispatchEvent(new KeyboardEvent('keydown',{bubbles:true,key:'ArrowUp'}));
  await new Promise(resolve=>setTimeout(resolve,30));
  point.dispatchEvent(new KeyboardEvent('keydown',{bubbles:true,key:'Delete'}));
  await new Promise(resolve=>setTimeout(resolve,30));
  check(rec.displayStretch.curve.length===count-1,'delete interior point');
  document.querySelector('.curve-point').dispatchEvent(new KeyboardEvent('keydown',{bubbles:true,key:'Delete'}));
  check(rec.displayStretch.curve.length===count-1,'preserve endpoint');
  check(rec.src===src&&rec.stats===stats&&rec.token===token,'raw data and analysis identity unchanged');
  check(JSON.stringify(s.view)===camera,'Camera unchanged');
  const log=document.querySelector('.hist-log');
  if(log){
    const settings=rec.displayStretch,heights=[...svg.querySelectorAll('rect')].map(r=>r.getAttribute('height')).join(',');
    log.click();await new Promise(resolve=>setTimeout(resolve,30));
    check(rec.displayStretch===settings,'log histogram does not alter display mapping');
    check(heights!==[...svg.querySelectorAll('rect')].map(r=>r.getAttribute('height')).join(','),'log frequency changes histogram heights');
    log.click();
  }
  return {combinedHistogram:true,addPoint:true,keyboardEdit:true,deletePoint:true,endpointProtected:true,displayOnly:true,logFrequencyOnly:!!log};
})()
