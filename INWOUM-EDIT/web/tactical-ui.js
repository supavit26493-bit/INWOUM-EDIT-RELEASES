// Presentation only: move existing DOM nodes; preserve IDs, values and handlers.
(() => {
 const picker=document.getElementById('neoAppearance');
 const modes=document.querySelector('.content-mode-panel');
 const workspace=document.getElementById('noobWorkspace');
 const create=workspace.querySelector('section[aria-label="เริ่มตัดต่อแบบง่าย"]');
 const result=workspace.querySelector('section[aria-label="สถานะและดาวน์โหลด XML"]');
 const singles=document.getElementById('singleOptions');
 const styles=document.getElementById('salesStyleOptions');
 const anchors=new Map();
 for(const node of [modes,workspace,singles,styles]){
  const anchor=document.createComment('tactical restore '+(node.id||'modes'));
  node.before(anchor);anchors.set(node,anchor);
 }
 create.classList.add('tactical-create');result.classList.add('tactical-result');
 const shell=document.createElement('div');shell.id='tacticalShell';shell.hidden=true;
 const hero=document.createElement('section');hero.className='tactical-hero';hero.setAttribute('aria-label','เลือกโหมดที่ต้องการใช้งาน');
 hero.innerHTML='<div class="tactical-hero-copy"><p class="tactical-kicker">CHOOSE YOUR EDIT MODE</p><h2>เลือกโหมด<br>ที่ต้องการใช้งาน<span>.</span></h2><p class="tactical-hero-hint">เลือกงานที่อยากทำ แล้วเริ่มสร้างคลิปได้เลย</p></div><div class="tactical-signal" aria-hidden="true"><b></b><em></em><small>SELECT / CREATE<br>MAKE THE CUT</small></div>';
 shell.append(hero);anchors.get(modes).before(shell);
 const sectionTitle=document.createElement('p');sectionTitle.className='tactical-mode-caption';sectionTitle.textContent='EDIT / MODES';modes.prepend(sectionTitle);
 function apply(){
  const active=['edit-lobby','focus-console'].includes(picker.value);
  if(active){
   if(modes.parentElement!==shell)shell.append(modes,workspace);
   const start=document.getElementById('noobStart');
   if(document.body.dataset.uiMode!=='pro'){
    if(singles.parentElement!==create)start.before(singles,styles);
   }else{
    for(const node of [singles,styles])anchors.get(node).after(node);
   }
   shell.hidden=false;
  }else{
   for(const [node,anchor]of anchors)anchor.after(node);
   shell.hidden=true;
  }
 }
 picker.addEventListener('change',apply);
 new MutationObserver(apply).observe(document.body,{attributes:true,attributeFilter:['data-ui-mode','data-content-mode']});apply();
})();
