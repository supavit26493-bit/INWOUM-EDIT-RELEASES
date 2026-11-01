// Super mode bridges shared NOOB/PRO controls to an isolated workflow.
(() => {
 const option=new Option('ทำ Super · สรุปใจความ → XML','super');$('contentMode').append(option);
 const options=document.createElement('div');options.id='superOptions';options.hidden=true;
 const label=document.createElement('label');label.htmlFor='superLayout';label.textContent='สัดส่วนภาพ Super';
 const layout=document.createElement('select');layout.id='superLayout';
 for(const [v,t] of [['auto','ตามขนาด XML อัตโนมัติ'],['landscape','แนวนอน · กึ่งกลางด้านล่าง'],['portrait','แนวตั้ง · กึ่งกลางด้านล่าง']])layout.append(new Option(t,v));
 const help=document.createElement('p');help.className='muted';help.textContent='สรุปใจความสั้น ๆ บรรทัดเดียว · Kanit Bold 100 สีเหลืองเข้ม · ไม่มีเงา · จัดกึ่งกลางแล้วเลื่อนลงด้วย Motion · ไม่เปลี่ยนภาพหรือเสียงต้นฉบับ';
 const hint=document.createElement('small');hint.textContent='CapCut ถอดเสียงเป็นท่อน → AI เลือกประเด็นและเกลาคำ 1 คำขอ → XML · ไม่ต้องจับเวลาคำบน GPU · ค่า API ตามโมเดลที่เลือก';
 const font=document.createElement('a');font.href='/super-font';font.download='Kanit-Bold.ttf';font.textContent='ดาวน์โหลดฟอนต์ Kanit สำหรับติดตั้งบนเครื่อง Premiere';font.className='button';font.style.margin='10px 0';
 const fontHint=document.createElement('small');fontHint.style.display='block';fontHint.textContent='เลือกแนวนอน/แนวตั้งให้ตรง XML โปรแกรมจัดข้อความตามขนาดเดิม ไม่ครอปภาพ · ฟอนต์ต้องติดตั้งบนเครื่องที่เปิดใน Premiere';
 options.append(label,layout,help,hint,font,fontHint);document.querySelector('.content-mode-panel').append(options);
 const hiddenControls=[$('profile').parentElement,$('mode'),document.querySelector('label[for=mode]'),$('transcription'),document.querySelector('label[for=transcription]'),$('asrHelp'),$('notes'),document.querySelector('label[for=notes]'),$('protected').closest('details'),$('demo'),$('demo').parentElement.nextElementSibling,...['assembleMap','groupPreview','textPreview','replan'].map(id=>$(id))].filter(Boolean).map(el=>[el,el.hidden]);
 const proIntro=$('xml').closest('section'),proTitle=proIntro.querySelector('h2'),proHelp=proIntro.querySelector(':scope > small');const originalProTitle=proTitle.textContent,originalProHelp=proHelp.textContent;
 const oldMode=updateContentMode;updateContentMode=function(){oldMode();const superMode=$('contentMode').value==='super';options.hidden=!superMode;
  for(const [el,wasHidden]of hiddenControls)el.hidden=superMode||wasHidden;
  proTitle.textContent=superMode?'เริ่มทำ Super':originalProTitle;proHelp.textContent=superMode?'XML ตามขนาดและเฟรมเรตต้นฉบับ · คงแทร็กภาพและเสียงเดิม':originalProHelp;
  document.querySelector('.noob-intro h2').textContent=superMode?'เลือก XML แล้วให้ AI ทำ Super':'เลือกไฟล์ แล้วให้ AI ตัดต่อ';
  if(superMode){$('mode').value='ai';$('mode').disabled=true;updateAsrSettings();document.querySelector('.noob-intro p').textContent='เลือกใจความจากเสียง แล้วสร้าง Super ที่แก้ข้อความต่อใน Premiere ได้';$('workflowHelp').textContent='Super บรรทัดเดียว · เลือกใจความก่อนเกลาคำ · แยกข้อสรุปกับข้อสงสัย · 1 คำขอ API';}
 };$('contentMode').onchange=updateContentMode;
 const oldConfig=config;config=function(){if($('contentMode').value==='super')return {content_mode:'super',xml:$('xml').value.trim(),relink:$('relink').value.trim(),audio_track:Number($('audio_track').value),model:$('model').value.trim(),effort:$('effort').value,api_key:$('api_key').value,mode:'ai',super_layout:layout.value};return oldConfig()};
 const oldShow=showConfig;showConfig=function(c){oldShow(c);if(c?.content_mode==='super'){layout.value=c.super_layout||'auto';updateContentMode()}};
 const oldConfirm=confirmCutStart;confirmCutStart=function(c){const superMode=c.content_mode==='super';$('cutConfirmTitle').textContent=superMode?'พร้อมทำ Super คลิปนี้ไหม?':'พร้อมตัดต่อคลิปนี้ไหม?';$('cutConfirmCover').hidden=superMode;$('cutConfirmCover').style.display=superMode?'none':'';$('cutConfirmCoverNote').hidden=superMode;
  if(!superMode)return oldConfirm(c);
  const serial=++cutConfirmSerial,dialog=$('cutConfirm');dialog.returnValue='';$('cutConfirmName').textContent=c.xml.replace(/\\/g,'/').split('/').pop();$('cutConfirmPath').textContent=c.xml;$('cutConfirmDuration').textContent='กำลังตรวจไฟล์…';$('cutConfirmModel').textContent=c.model+' · '+c.effort;$('cutConfirmMode').textContent='Super บรรทัดเดียว · Kanit สีเหลืองเข้ม · 1 API';$('cutConfirmError').hidden=true;$('cutConfirmYes').disabled=true;dialog.showModal();
  const answer=new Promise(resolve=>dialog.addEventListener('close',()=>resolve(dialog.returnValue==='yes'&&!$('cutConfirmYes').disabled),{once:true}));
  api('/api/inspect',{content_mode:'super',xml:c.xml,relink:c.relink,audio_track:c.audio_track,super_layout:c.super_layout}).then(info=>{if(!dialog.open||serial!==cutConfirmSerial)return;$('cutConfirmDuration').textContent=minuteClock(info.seconds,true)+' นาที:วินาที · '+info.width+'×'+info.height;$('cutConfirmYes').disabled=false}).catch(e=>{if(!dialog.open||serial!==cutConfirmSerial)return;$('cutConfirmError').textContent=e.message;$('cutConfirmError').hidden=false});return answer;
 };
 const oldRun=$('run').onclick;$('run').onclick=event=>{if($('contentMode').value!=='super')return oldRun(event);return action(async()=>{if(startConfirmationBusy)return;const c=config();if(!c.xml)throw Error('เลือก XML ก่อนทำ Super');if(!c.api_key.trim())throw Error('ใส่ API key ก่อนทำ Super');startConfirmationBusy=true;try{if(!await confirmCutStart(c))return;const j=await api('/api/super',c);await select(j.id)}finally{startConfirmationBusy=false}})};
 const panel=document.createElement('section');panel.id='superResults';panel.className='panel';panel.hidden=true;$('results').after(panel);
 function resultPanel(d){panel.hidden=d?.config?.content_mode!=='super'||!d?.['super-result'];if(panel.hidden)return;panel.replaceChildren();const r=d['super-result'];
  const title=document.createElement('h2');title.textContent='Super · '+r.count+' ข้อความ';const note=document.createElement('p');note.className='muted';note.textContent='บรรทัดเดียว · '+r.font+' · กึ่งกลางช่วงล่าง · '+(r.edited?'แก้ข้อความและเวลาแล้ว':'ข้อความจาก API')+' · ตรวจหน้าตาใน Premiere ก่อนใช้จริง';const actions=document.createElement('div');actions.className='actions';
  for(const [text,path]of [['ดาวน์โหลด XML พร้อมต้นฉบับ',r.combined_file],['ดาวน์โหลด XML เฉพาะ Super',r.text_file],['รายการข้อความ CSV',r.csv_file]]){const a=link(text,path);if(path===r.combined_file)a.classList.add('primary','inwoum-download-ready');actions.append(a)}
  const table=document.createElement('table');for(const x of r.items){const tr=document.createElement('tr');for(const v of [time(x.start)+'–'+time(x.end),x.text]){const td=document.createElement('td');td.textContent=v;tr.append(td)}table.append(tr)}panel.append(title,note,actions,table);
 }
 const oldResult=showResult;showResult=function(d){if(d?.config?.content_mode==='super'){for(const id of ['results','editor','sourceCoverage','groupCheckpoint','transcriptCheckpoint','draftCheckpoint'])if($(id))$(id).hidden=true;resultPanel(d);return}panel.hidden=true;oldResult(d)};
 const textOnly=document.createElement('a');textOnly.id='noobSuperText';textOnly.className='button';textOnly.textContent='ดาวน์โหลดเฉพาะ Super';textOnly.hidden=true;$('noobDownload').after(textOnly);
 const countLabel=document.createElement('span');$('noobClips').parentElement.lastChild.replaceWith(countLabel);
 const oldNoob=renderNoob;renderNoob=function(){oldNoob();const mode=$('contentMode').value==='super';if(mode)$('noobStartText').textContent=$('run').disabled?'กำลังทำ Super…':'เริ่มทำ Super';const job=noobDisplayJob(),r=detail?.job?.id===job?.id?detail?.['super-result']:null;const ready=!!r&&job?.status==='done';textOnly.hidden=!ready;
  countLabel.textContent=mode?'ข้อความ Super':'เวอร์ชัน';
  if(ready){$('noobClips').textContent=r.count;$('noobDownload').setAttribute('aria-disabled','false');$('noobDownload').href=file(r.combined_file);$('noobDownload').download='SUPER-WITH-ORIGINAL.xml';textOnly.href=file(r.text_file);textOnly.download='SUPER-ONLY.xml'}
 };
 const oldClear=clearProjectView;clearProjectView=function(){oldClear();panel.hidden=true;textOnly.hidden=true};
 const oldRefresh=refresh;refresh=async function(...args){await oldRefresh(...args);if($('contentMode').value==='super'||detail?.config?.content_mode==='super')for(const id of ['assembleMap','groupPreview','textPreview','replan','loadPlan','retryUnknown'])if($(id))$(id).hidden=true;layout.disabled=$('run').disabled};
 updateContentMode();renderNoob();
})();
