// Appearance only. Read existing job-state attributes; never start work or call APIs.
(() => {
 const key='inwoum.mascot.v1',html=document.documentElement;
 let choice='round';try{const saved=localStorage.getItem(key);if(['round','slime'].includes(saved))choice=saved}catch{}
 const control=document.createElement('div');control.className='inwoum-theme-control';
 const label=document.createElement('label');label.htmlFor='neoMascot';label.textContent='มาสคอต';
 const select=document.createElement('select');select.id='neoMascot';select.append(new Option('ตัวกลม','round'),new Option('สไลม์','slime'));select.value=choice;control.append(label,select);
 document.querySelector('.neo-header-fields').append(control);
 const rows=[[20,21],[18,21],[15,20],[12,20],[10,21],[8,23],[7,24],[6,25],[5,26],[5,26],[5,26],[6,25],[7,24],[9,22],[12,19]];
 const entries=[],reduced=matchMedia('(prefers-reduced-motion: reduce)');
 function mix(c,t,k){return '#'+c.map((v,i)=>Math.round(v+(t[i]-v)*k).toString(16).padStart(2,'0')).join('')}
 function palette(){
  const theme=html.dataset.neoTheme;
  if(theme==='edit-lobby')return [255,154,157];
  if(theme==='focus-console')return [131,222,201];
  if(theme==='white-signal')return [133,164,255];
  if(theme==='grassland')return [163,215,92];
  const value=getComputedStyle(html).getPropertyValue('--green').trim(),probe=document.createElement('span');
  probe.style.color=value;probe.hidden=true;document.body.append(probe);const color=getComputedStyle(probe).color;probe.remove();
  const rgb=color.match(/[\d.]+/g)?.slice(0,3).map(Number);return rgb?.length===3?rgb:[180,223,131];
 }
 let colors=[];
 function draw(entry,time){
  const {canvas,row}=entry,ctx=canvas.getContext('2d'),phase=row.dataset.state||'idle';
  ctx.clearRect(0,0,96,68);ctx.imageSmoothingEnabled=false;
  const [base,light,shade]=colors,ink='#263039';
  const step=Math.floor(time*6),bounce=phase==='working'?[0,0,-1,-3,-2,0][step%6]:phase==='done'?[0,-1,-2,-1,0,0][step%6]:phase==='idle'?[0,0,0,-1,-1,0][Math.floor(time*2)%6]:0;
  const squash=phase==='working'&&step%6===0;
  const cell=(x,y,w,h,color)=>{ctx.fillStyle=color;ctx.fillRect(x*3,(y+bounce)*3,w*3,h*3)};
  rows.forEach(([a,b],i)=>{const y=5+i,yy=squash?6+Math.round(i*.9):y,extra=squash&&i>7?1:0;cell(a-extra,yy,b-a+1+extra*2,1,i>11?shade:base)});
  cell(11,10+(squash?1:0),4,1,light);cell(10,11+(squash?1:0),2,1,light);
  const faceY=12+(squash?1:0),blink=phase==='idle'&&time%5>4.65,look=phase==='idle'&&Math.floor(time/2)%3===1?1:0;
  function eye(x){if(phase==='done'){cell(x,faceY+1,1,1,ink);cell(x+1,faceY,1,1,ink);cell(x+2,faceY+1,1,1,ink)}else if(blink){cell(x,faceY+2,3,1,ink)}else{cell(x+look,faceY,3,3,ink);cell(x+look,faceY,1,1,'#fff6e8')}}
  eye(10);eye(20);
  if(phase==='error'){cell(15,faceY+4,3,1,ink)}else{cell(15,faceY+4,1,1,ink);cell(16,faceY+5,1,1,ink);cell(17,faceY+4,1,1,ink)}
  cell(24,3+(Math.floor(time*2)%2),1,1,base);
 }
 const observer=new IntersectionObserver(items=>{for(const item of items){const entry=entries.find(e=>e.canvas===item.target);if(entry)entry.visible=item.isIntersecting}});
 for(const row of document.querySelectorAll('.inwoum-mascot-row')){
  const canvas=document.createElement('canvas');canvas.width=96;canvas.height=68;canvas.className='neo-slime-canvas';canvas.setAttribute('aria-hidden','true');row.prepend(canvas);
  const entry={canvas,row,visible:false};entries.push(entry);observer.observe(canvas);
  new MutationObserver(()=>{if(choice==='slime')draw(entry,reduced.matches?0:performance.now()/1000)}).observe(row,{attributes:true,attributeFilter:['data-state']});
 }
 function paint(){const rgb=palette();colors=[mix(rgb,rgb,0),mix(rgb,[255,255,255],.42),mix(rgb,[87,73,77],.26)];if(choice==='slime')for(const entry of entries)draw(entry,0)}
 function apply(){html.dataset.neoMascot=choice;if(choice==='slime')paint()}
 select.addEventListener('change',()=>{choice=select.value;apply();try{localStorage.setItem(key,choice)}catch{}});
 new MutationObserver(()=>{if(choice==='slime')paint()}).observe(html,{attributes:true,attributeFilter:['data-neo-theme','data-inwoum-theme']});
 apply();reduced.addEventListener('change',()=>{if(choice==='slime')paint()});
 // Sleep completely when hidden, offscreen, or when the original mascot is selected.
 let timer=null;
 function schedule(){if(timer!==null)return;timer=setTimeout(()=>{timer=null;if(document.hidden||choice!=='slime')return;const time=reduced.matches?0:performance.now()/1000;for(const entry of entries)if(entry.visible)draw(entry,time);if(!reduced.matches)schedule()},150)}
 select.addEventListener('change',()=>{if(choice==='slime')schedule();else{clearTimeout(timer);timer=null}});
 document.addEventListener('visibilitychange',()=>{if(document.hidden){clearTimeout(timer);timer=null}else if(choice==='slime')schedule()});
 reduced.addEventListener('change',()=>{if(!reduced.matches&&choice==='slime')schedule()});if(choice==='slime')schedule();
})();
