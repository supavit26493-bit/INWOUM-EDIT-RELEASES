// Local Super editor; submits only text/timing edits, never an API key.
function superEditorMatchesContext({mode,xml,jobId,detail,fresh,noobReady=true}){
 const path=value=>String(value||'').trim().replace(/\\/g,'/').replace(/\/+/g,'/').toLowerCase();
 return !fresh&&mode==='super'&&noobReady&&detail?.config?.content_mode==='super'&&
  !!detail['super-result']&&!!jobId&&detail.job?.id===jobId&&
  !!path(xml)&&path(xml)===path(detail.config.xml);
}
(()=>{
 const panel=document.createElement('section');panel.id='superEditor';panel.hidden=true;
 const css=document.createElement('style');css.textContent=`
 @font-face{font-family:InwoumSuper;src:url('/super-font') format('truetype');font-weight:700}
 #superEditor{margin-top:24px;border-top:1px solid var(--line);padding-top:20px;min-width:0}
 #superEditor h2{margin:0;font-size:20px}#superEditor h3{margin:0 0 12px;font-size:15px}
 #superEditor .se-head{display:flex;justify-content:space-between;gap:12px;align-items:center;flex-wrap:wrap;margin-bottom:14px}
 #superEditor .se-grid{display:grid;grid-template-columns:minmax(0,.9fr) minmax(0,1.1fr);gap:16px}
 #superEditor .se-list{display:flex;flex-direction:column;gap:8px;max-height:540px;overflow:auto;padding:0 4px 4px 0}
 #superEditor .se-item{text-align:left;display:flex;gap:10px;align-items:flex-start;width:100%;margin:0;padding:12px}
 #superEditor .se-item[aria-pressed=true]{border-color:var(--green);background:color-mix(in srgb,var(--green) 12%,var(--panel))}
 #superEditor .se-item span{min-width:0;overflow-wrap:anywhere}#superEditor .se-item b{color:var(--green);font:12px Consolas,monospace;padding-top:3px}
 #superEditor .se-item strong{font-size:13px;font-weight:400;display:block}#superEditor .se-item small{font:11px Consolas,monospace}
 #superEditor .se-form{background:var(--panel);padding:16px;border:1px solid var(--line);min-width:0}
 #superEditor input{width:100%;min-width:0}#superEditor label{font-size:12px}
 #superEditor .se-times{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin:14px 0}
 #superEditor .se-preview{margin:12px 0;padding:18px 10px;background:#0d141d;text-align:center;overflow:hidden}
 #superEditor .se-preview span{display:inline-block;white-space:nowrap;font:700 20px InwoumSuper,Kanit,sans-serif;color:#e8b923;transform-origin:center}
 #superEditor .se-meta{display:flex;justify-content:space-between;gap:10px;align-items:center;flex-wrap:wrap}
 #superEditor .se-meta small{font-size:11px}#superEditor .se-meta button{font-size:12px}
 #superEditor .se-error{color:#ff9e9e}#superEditor .se-note{color:var(--green);font-size:12px}
 #superEditor .se-footer{display:flex;gap:12px;align-items:center;justify-content:space-between;flex-wrap:wrap;margin-top:16px}
 #superEditor audio{width:100%;margin-top:10px}#superEditor .se-empty{color:var(--muted);padding:20px 0}
 @media(max-width:680px){#superEditor .se-grid{grid-template-columns:1fr}#superEditor .se-list{max-height:260px}#superEditor input{font-size:16px}#superEditor .se-footer .primary{width:100%}}
 `;document.head.append(css);
 const drafts=new Map();let state=null,busy=false,observer=null,audio=null;
 const policy='later-in-shorter-out-v1';
 const make=(tag,text,cls)=>{const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e};
 const snap=(v,fps)=>Math.round(v*fps)/fps;
 const count=t=>Array.from(t).filter(c=>!/[\u0e31\u0e34-\u0e3a\u0e47-\u0e4e]/.test(c)).length;
 function initialize(d){const r=d['super-result'],fps=r.fps||50;const key=d.job.id+'|'+r.revision;
  if(drafts.has(key))return drafts.get(key);
  const rows=r.items.map((r,index)=>({index,text:r.text,start:r.start,end:r.end}));
  const legacy=r.timing_adjustment?.policy!==policy;
  if(legacy)for(const r of rows){const a=Math.round(r.start*fps),b=Math.round(r.end*fps),minimum=Math.min(b-a,Math.max(1,Math.ceil(1.5*fps)));const first=Math.min(a+Math.round(.3*fps),b-minimum),last=Math.max(first+minimum,b-Math.round(fps));r.start=first/fps;r.end=last/fps}
  const s={id:d.job.id,revision:r.revision,rows,selected:rows[0]?.index,seconds:r.seconds,fps,dirty:legacy,legacy,undo:null};drafts.set(key,s);return s;
 }
 function error(){if(!state?.rows.length)return 'ต้องเหลือ Super อย่างน้อย 1 ข้อความ';let previous=0;
  for(let i=0;i<state.rows.length;i++){const r=state.rows[i];if(!r.text.trim()||/[\r\n\x00-\x1f]/.test(r.text))return `ข้อความ ${i+1} ต้องเป็นบรรทัดเดียวและไม่ว่าง`;if(count(r.text)>46)return `ข้อความ ${i+1} ยาวเกินไป กรุณาย่อ`;if(!Number.isFinite(r.start)||!Number.isFinite(r.end)||!(r.start>=0&&r.start<r.end&&r.end<=state.seconds+.001))return `ตรวจเวลาเข้า–ออกของข้อความ ${i+1}`;const a=Math.round(r.start*state.fps),b=Math.round(r.end*state.fps);if(a<previous||b<=a)return `ข้อความ ${i+1} เวลาซ้อนหรือสั้นกว่า 1 เฟรม`;previous=b}return '';
 }
 let status,saveButton,undoButton,form,list;
 function update(){const clientError=error(),message=clientError||state.serverError;status.textContent=message||(state.dirty?`${state.rows.length} ข้อความ · มีการแก้ไข กดสร้าง XML ใหม่ก่อนดาวน์โหลด`:`${state.rows.length} ข้อความ · XML ตรงกับรายการที่บันทึกไว้`);status.className=message?'se-error':'muted';saveButton.disabled=busy||!!clientError||!state.dirty||$('run').disabled;undoButton.disabled=busy||!state.undo;panel.querySelectorAll('input,.se-item,.se-delete,.se-listen').forEach(e=>e.disabled=busy||$('run').disabled)}
 function change(row,key,value){row[key]=key==='text'?value:(value===''?NaN:Number(value));state.serverError='';state.dirty=true;update();const title=list.querySelector(`[data-index="${row.index}"] strong`),time=list.querySelector(`[data-index="${row.index}"] small`);title.textContent=row.text;time.textContent=display(row.start)+' — '+display(row.end)}
 function display(n){return Number.isFinite(n)?minuteClock(n,true):'—'}
 function renderForm(){audio?.pause();observer?.disconnect();form.replaceChildren();const row=state.rows.find(r=>r.index===state.selected);if(!row){form.append(make('p','ไม่มีข้อความที่เลือก','se-empty'));return}
  form.append(make('h3','ข้อความ '+String(row.index+1).padStart(2,'0')));
  const label=make('label','แก้ข้อความ · บรรทัดเดียว');label.htmlFor='superEditText';const text=make('input');text.id='superEditText';text.value=row.text;text.type='text';form.append(label,text);
  const meta=make('div',undefined,'se-meta'),counter=make('small',count(row.text)+' ตัวอักษร');meta.append(counter,make('small','Kanit Bold · สีเหลือง'));form.append(meta);
  const times=make('div',undefined,'se-times');for(const [key,title]of [['start','เข้า (วินาที)'],['end','ออก (วินาที)']]){const group=make('div'),lab=make('label',title),field=make('input');lab.htmlFor='superEdit-'+key;field.id=lab.htmlFor;field.type='number';field.min='0';field.max=String(state.seconds);field.step='.01';field.value=row[key].toFixed(3);field.oninput=()=>{change(row,key,field.value);duration.textContent=Number.isFinite(row.end-row.start)?'แสดง '+(row.end-row.start).toFixed(2)+' วินาที':''};group.append(lab,field);times.append(group)}form.append(times);
  const preview=make('div',undefined,'se-preview'),previewText=make('span',row.text);preview.setAttribute('aria-label','ตัวอย่างข้อความ Super');preview.append(previewText);form.append(preview);
  const fit=()=>{previewText.style.transform='';const w=preview.clientWidth-24;if(w>0&&previewText.offsetWidth>w)previewText.style.transform='scale('+w/previewText.offsetWidth+')'};observer=new ResizeObserver(fit);observer.observe(preview);
  text.oninput=()=>{change(row,'text',text.value);counter.textContent=count(text.value)+' ตัวอักษร';previewText.textContent=text.value;fit()};
  const actions=make('div',undefined,'se-meta'),duration=make('small','แสดง '+(row.end-row.start).toFixed(2)+' วินาที'),remove=make('button','ลบข้อความนี้','se-delete');remove.type='button';remove.onclick=()=>{state.serverError='';state.undo={rows:JSON.parse(JSON.stringify(state.rows)),selected:state.selected};state.rows=state.rows.filter(r=>r.index!==row.index);state.selected=state.rows[0]?.index;state.dirty=true;draw()};actions.append(duration,remove);form.append(actions);
  const listen=make('button','ฟังเสียงต้นฉบับช่วงนี้','se-listen');listen.type='button';audio=make('audio');audio.controls=true;audio.preload='none';audio.src='/files/'+encodeURIComponent(state.id)+'/timeline.wav?token='+encodeURIComponent(TOKEN);listen.onclick=()=>{audio.currentTime=Math.max(0,row.start||0);audio.play().catch(e=>{status.textContent='เปิดเสียงไม่ได้: '+e.message;status.className='se-error'})};audio.ontimeupdate=()=>{if(!audio.paused&&audio.currentTime>=row.end)audio.pause()};form.append(listen,audio);
 }
 function draw(){audio?.pause();observer?.disconnect();panel.replaceChildren();const header=make('div',undefined,'se-head');header.append(make('h2','แก้ Super'),make('small','ไม่เรียก API · ไม่เสียค่า AI เพิ่ม','se-note'));panel.append(header);
  const timing=make('p',state.legacy?'งานเก่า: เตรียมเลื่อนเข้า +0.3 วิ / ตัดท้าย 1 วิแล้ว กดสร้าง XML ใหม่เพื่อใช้ค่าใหม่':'เลื่อนเข้า +0.3 วิ / ตัดท้าย 1 วิแล้ว · แก้เวลาเองได้ และไม่หักซ้ำเมื่อบันทึก','se-note');panel.append(timing);
  const grid=make('div',undefined,'se-grid');list=make('div',undefined,'se-list');form=make('div',undefined,'se-form');grid.append(list,form);panel.append(grid);
  for(const row of state.rows){const b=make('button',undefined,'se-item');b.type='button';b.dataset.index=row.index;b.setAttribute('aria-pressed',String(row.index===state.selected));const text=make('span');text.append(make('strong',row.text),make('small',display(row.start)+' — '+display(row.end)));b.append(make('b',String(row.index+1).padStart(2,'0')),text);b.onclick=()=>{state.selected=row.index;draw()};list.append(b)}
  status=make('p');status.setAttribute('aria-live','polite');panel.append(status);const footer=make('div',undefined,'se-footer');undoButton=make('button','ย้อนคืนรายการที่ลบ');undoButton.type='button';undoButton.onclick=()=>{if(state.undo){state.serverError='';state.rows=state.undo.rows;state.selected=state.undo.selected;state.undo=null;state.dirty=true;draw()}};saveButton=make('button','สร้าง XML ใหม่ · ไม่เสีย API','primary');saveButton.type='button';saveButton.onclick=save;footer.append(undoButton,saveButton);panel.append(footer,make('small','สร้าง revision ใหม่ เก็บ XML เดิมไว้ · คงตัวคั่นเฟรม 0 · เวลาอิงท่อนเสียง สามารถปรับละเอียดได้'));
  renderForm();update();
 }
 async function save(){if(busy||error())return;busy=true;audio?.pause();update();const target=state;
  try{await api('/api/super-save',{id:target.id,base_revision:target.revision,items:target.rows,timing_policy:policy});await refresh(true)}catch(e){target.serverError=e.message;notice(e)}finally{busy=false;if(state)update()}
 }
 function contextMatches(){const noob=document.body.dataset.uiMode==='noob';return superEditorMatchesContext({mode:$('contentMode').value,xml:$(noob?'noobXml':'xml').value,jobId:current,detail,fresh:uiFreshProject,noobReady:!noob||noobDisplayJob()?.id===current})}
 function refreshEditor(){const d=detail;if(!contextMatches()){panel.hidden=true;audio?.pause();return}
  const parent=document.body.dataset.uiMode==='pro'?$('superResults'):$('noobDownload').closest('section');if(parent&&!parent.contains(panel))parent.append(panel);panel.hidden=false;
  const next=initialize(d);if(state!==next){audio?.pause();state=next;draw()}else update();
 }
 const oldRefresh=refresh;refresh=async function(...args){await oldRefresh(...args);refreshEditor()};
 const oldSelect=select;select=async function(...args){panel.hidden=true;audio?.pause();try{return await oldSelect(...args)}finally{refreshEditor()}};
 const oldContentMode=updateContentMode;updateContentMode=function(...args){oldContentMode(...args);refreshEditor()};$('contentMode').onchange=updateContentMode;
 for(const id of ['xml','noobXml'])for(const event of ['input','change'])$(id).addEventListener(event,refreshEditor);
 const xmlPicker=document.querySelector('[data-pick="xml"]'),oldPick=xmlPicker.onclick;xmlPicker.onclick=async function(...args){try{return await oldPick.apply(this,args)}finally{refreshEditor()}};
 const oldMode=setUIMode;setUIMode=function(...args){oldMode(...args);refreshEditor()};
 const oldClear=clearProjectView;clearProjectView=function(){audio?.pause();oldClear();panel.hidden=true;state=null};
 document.addEventListener('click',event=>{if(!state?.dirty||panel.hidden)return;const a=event.target.closest('#noobDownload,#noobSuperText,#superResults a');if(a){event.preventDefault();status.textContent='มีการแก้ไข Super กรุณากดสร้าง XML ใหม่ก่อนดาวน์โหลด';status.className='se-error';panel.scrollIntoView({behavior:'smooth',block:'nearest'})}},true);
 window.addEventListener('beforeunload',event=>{if(state?.dirty&&!panel.hidden&&contextMatches()){event.preventDefault();event.returnValue=''}});
 refreshEditor();
})();
