// Decorative only: observe existing mascot state; no job, API or audio changes.
(() => {
  'use strict';
  const html=document.documentElement,select=document.getElementById('neoMascot');
  if(!select)return;
  const types=['cat','slime_soft','bot'],key='inwoum.mascot.v1';
  select.querySelector('option[value="slime"]').textContent='สไลม์ · Pixel';
  for(const [value,label] of [['cat','CAT · แมว'],['slime_soft','SLIME · นุ่มนิ่ม'],['bot','BOT · ผู้ช่วย']])select.append(new Option(label,value));
  const reduced=matchMedia('(prefers-reduced-motion: reduce)'),entries=[];
  const clamp=(v,a,b)=>Math.max(a,Math.min(b,v));
  let choice=select.value,pointer=null,frame=0,previous=0,layoutDirty=true;
  let palette={body:'#a9d9b4',shade:'#84ac90',cream:'#fff0d4',ink:'#1d2931',white:'#f7fff6'};
  const active=()=>types.includes(choice);
  const mix=(rgb,target,k)=>'#'+rgb.map((v,i)=>Math.round(v+(target[i]-v)*k).toString(16).padStart(2,'0')).join('');
  function theme(){
    const probe=document.createElement('span');probe.style.color=getComputedStyle(html).getPropertyValue('--green').trim()||'#a9d9b4';probe.hidden=true;document.body.append(probe);
    const rgb=getComputedStyle(probe).color.match(/[\d.]+/g)?.slice(0,3).map(Number)||[169,217,180];probe.remove();
    palette={...palette,body:mix(rgb,[235,246,227],.24),shade:mix(rgb,[69,91,80],.25)};
  }
  function draw(entry,t){
    const {canvas}=entry,ctx=canvas.getContext('2d'),p=palette,phase=entry.row.dataset.state||'idle';
    if(phase!==entry.phase){entry.phase=phase;entry.doneAt=phase==='done'?t:-10000;}
    if(t>entry.blinkAt&&!reduced.matches){entry.blinkStart=t;entry.blinkAt=t+2900+Math.random()*2400;}
    const elapsed=t-entry.blinkStart,blink=!reduced.matches&&elapsed>=0&&elapsed<175?Math.sin(elapsed/175*Math.PI):0;
    const happy=phase==='done'&&t-entry.doneAt<1800&&!reduced.matches;
    const cx=entry.box.left+entry.box.width/2,cy=entry.box.top+entry.box.height*.56;
    const tx=pointer?clamp((pointer.x-cx)/190,-1,1)*3.6:0,ty=pointer?clamp((pointer.y-cy)/190,-1,1)*2.6:0;
    entry.x+=(tx-entry.x)*(reduced.matches?1:.22);entry.y+=(ty-entry.y)*(reduced.matches?1:.22);
    ctx.clearRect(0,0,280,248);ctx.save();ctx.scale(2,2);
    const bob=reduced.matches?0:phase==='working'?-Math.abs(Math.sin(t/240))*5.5:phase==='done'&&t-entry.doneAt<1800?-Math.abs(Math.sin((t-entry.doneAt)/180))*4:Math.sin(t/1200+entry.seed)*1.25;
    ctx.translate(70,77+bob);ctx.rotate(reduced.matches?0:entry.x*.007);ctx.translate(-70,-77);
    ctx.lineWidth=3.3;ctx.lineJoin='round';ctx.lineCap='round';
    function shape(fill,fn){ctx.beginPath();fn();ctx.fillStyle=fill;ctx.fill();ctx.strokeStyle=p.ink;ctx.stroke();}
    function rect(x,y,w,h,r){ctx.roundRect(x,y,w,h,r);}
    if(choice==='cat'){
      const twitch=reduced.matches?0:Math.pow(Math.max(0,Math.sin(t/830+entry.seed)),28)*1.8;
      shape(p.body,()=>{ctx.moveTo(22,57);ctx.quadraticCurveTo(11,10+twitch,23,10);ctx.quadraticCurveTo(39,12,50,32);ctx.lineTo(92,32);ctx.quadraticCurveTo(107,10,119,12);ctx.quadraticCurveTo(130,12+twitch,118,59);ctx.quadraticCurveTo(133,77,121,98);ctx.quadraticCurveTo(103,117,71,115);ctx.quadraticCurveTo(36,117,18,97);ctx.quadraticCurveTo(6,78,22,57);});
      ctx.fillStyle=p.cream;ctx.beginPath();ctx.moveTo(24,20);ctx.lineTo(28,46);ctx.lineTo(43,33);ctx.closePath();ctx.fill();ctx.beginPath();ctx.moveTo(115,22);ctx.lineTo(99,34);ctx.lineTo(114,47);ctx.closePath();ctx.fill();
      ctx.fillStyle=p.shade;for(const x of [59,75]){ctx.beginPath();ctx.moveTo(x,35);ctx.quadraticCurveTo(x+3,53,x+6,35);ctx.fill();}
      ctx.fillStyle=p.cream;ctx.beginPath();ctx.moveTo(70,77);ctx.bezierCurveTo(54,89,30,83,30,99);ctx.bezierCurveTo(43,118,100,118,111,99);ctx.bezierCurveTo(108,84,86,88,70,77);ctx.fill();
    }else if(choice==='slime_soft'){
      shape(p.body,()=>{ctx.moveTo(24,61);ctx.bezierCurveTo(38,43,77,36,91,12);ctx.quadraticCurveTo(98,4,104,18);ctx.bezierCurveTo(110,41,133,65,126,91);ctx.bezierCurveTo(122,114,96,116,70,116);ctx.bezierCurveTo(36,116,12,110,13,87);ctx.quadraticCurveTo(13,72,24,61);});
      ctx.fillStyle=p.cream;ctx.beginPath();ctx.ellipse(92,27,5,8,.7,0,Math.PI*2);ctx.fill();
    }else{
      shape(p.body,()=>rect(53,11,36,12,6));shape(p.body,()=>rect(8,57,17,31,7));shape(p.body,()=>rect(117,57,16,31,7));
      shape(p.cream,()=>rect(19,24,104,91,26));ctx.fillStyle=p.ink;ctx.beginPath();rect(29,39,84,64,18);ctx.fill();
    }
    const eyeY=choice==='bot'?69:73;
    for(const x of [47,94]){
      if(happy||blink>.85){ctx.strokeStyle=choice==='bot'?p.body:p.ink;ctx.lineWidth=3.6;ctx.beginPath();ctx.moveTo(x-8,eyeY+3);ctx.quadraticCurveTo(x,eyeY-6,x+8,eyeY+3);ctx.stroke();continue;}
      ctx.save();ctx.translate(x,eyeY);ctx.scale(1,Math.max(.06,1-blink));
      ctx.fillStyle=choice==='bot'?p.body:p.white;ctx.beginPath();ctx.ellipse(0,0,11.2,13.5,0,0,Math.PI*2);ctx.fill();
      ctx.save();ctx.beginPath();ctx.ellipse(0,0,10.3,12.8,0,0,Math.PI*2);ctx.clip();
      ctx.fillStyle=p.ink;ctx.beginPath();ctx.ellipse(entry.x,entry.y,8.4,11,0,0,Math.PI*2);ctx.fill();
      ctx.fillStyle=p.white;ctx.beginPath();ctx.arc(entry.x-2.4,entry.y-4.1,2.7,0,Math.PI*2);ctx.fill();ctx.restore();ctx.restore();
    }
    const mouthY=choice==='bot'?87:97;
    if(choice==='cat'){ctx.fillStyle=p.ink;ctx.beginPath();ctx.moveTo(67,88);ctx.quadraticCurveTo(71,86,75,88);ctx.lineTo(71,91.5);ctx.closePath();ctx.fill();}
    ctx.strokeStyle=choice==='bot'?p.body:p.ink;ctx.lineWidth=2.5;ctx.beginPath();ctx.moveTo(63,mouthY);
    if(phase==='error')ctx.quadraticCurveTo(71,mouthY-8,79,mouthY);
    else if(choice==='cat'&&phase!=='done'){ctx.quadraticCurveTo(67,mouthY+5,71,mouthY-1);ctx.quadraticCurveTo(75,mouthY+5,79,mouthY);}
    else ctx.quadraticCurveTo(71,mouthY+(phase==='done'?12:8),79,mouthY);
    ctx.stroke();ctx.restore();
    // Non-sensitive diagnostics for UI regression checks; never read by work code.
    canvas.dataset.gazeX=entry.x.toFixed(2);canvas.dataset.gazeY=entry.y.toFixed(2);canvas.dataset.phase=phase;canvas.dataset.kind=choice;
  }
  function render(t){
    frame=0;if(!active()||document.hidden)return;
    if(layoutDirty){for(const e of entries)if(e.visible)e.box=e.canvas.getBoundingClientRect();layoutDirty=false;}
    if(t-previous>=32||reduced.matches){for(const e of entries)if(e.visible)draw(e,t);previous=t;}
    if(!reduced.matches&&entries.some(e=>e.visible))frame=requestAnimationFrame(render);
  }
  function wake(){if(!frame&&active()&&!document.hidden)frame=requestAnimationFrame(render);}
  const observer=new IntersectionObserver(changes=>{for(const change of changes){const e=entries.find(e=>e.canvas===change.target);if(e)e.visible=change.isIntersecting;}layoutDirty=true;wake();});
  for(const row of document.querySelectorAll('.inwoum-mascot-row')){
    const canvas=document.createElement('canvas');canvas.width=280;canvas.height=248;canvas.className='neo-companion-canvas';canvas.setAttribute('aria-hidden','true');row.prepend(canvas);
    const entry={canvas,row,visible:false,box:{left:0,top:0,width:72,height:64},x:0,y:0,seed:entries.length*.7,phase:'',doneAt:-10000,blinkStart:-1000,blinkAt:performance.now()+2100+entries.length*500};entries.push(entry);observer.observe(canvas);
    new MutationObserver(wake).observe(row,{attributes:true,attributeFilter:['data-state']});
  }
  function apply(){choice=select.value;html.dataset.neoMascot=choice;layoutDirty=true;previous=0;for(const e of entries){e.x=0;e.y=0;e.phase='';}cancelAnimationFrame(frame);frame=0;if(active()){theme();wake();}}
  select.addEventListener('change',apply);
  let saved;try{saved=localStorage.getItem(key);}catch{}
  if(types.includes(saved)){select.value=saved;select.dispatchEvent(new Event('change'));}else apply();
  document.addEventListener('pointermove',e=>{if(e.pointerType!=='mouse'||!active())return;pointer={x:e.clientX,y:e.clientY};wake();},{passive:true});
  document.addEventListener('pointerout',e=>{if(e.relatedTarget===null){pointer=null;wake();}},{passive:true});
  window.addEventListener('blur',()=>{pointer=null;wake();});
  window.addEventListener('scroll',()=>{layoutDirty=true;wake();},{passive:true});
  window.addEventListener('resize',()=>{layoutDirty=true;wake();},{passive:true});
  document.addEventListener('visibilitychange',()=>{if(document.hidden){cancelAnimationFrame(frame);frame=0;}else{layoutDirty=true;wake();}});
  reduced.addEventListener('change',()=>{cancelAnimationFrame(frame);frame=0;previous=0;wake();});
  new MutationObserver(()=>{if(active()){theme();wake();}}).observe(html,{attributes:true,attributeFilter:['data-neo-theme','data-inwoum-theme']});
})();
