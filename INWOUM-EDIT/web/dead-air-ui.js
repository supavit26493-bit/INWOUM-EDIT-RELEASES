/* Local-only workspace. It never reads or sends an API key. */
(()=>{
 const mode=$('contentMode');mode.append(new Option('ตัด Dead Air · ในเครื่อง · ไม่ใช้ token','dead_air'));
 const style=document.createElement('style');style.textContent=`
 #deadAirWorkspace{display:none;max-width:1040px;margin:24px auto;padding:0 24px}
 body[data-content-mode=dead_air] #deadAirWorkspace{display:block}
 body[data-content-mode=dead_air] #noobWorkspace,body[data-content-mode=dead_air] #proWorkspace,body[data-content-mode=dead_air] #subtitleWorkspace{display:none!important}
 #deadAirWorkspace .dead-grid{display:grid;grid-template-columns:1fr 1fr;gap:24px}
 #deadAirWorkspace label{display:block;margin-top:18px}#deadAirWorkspace .dead-file{display:flex;gap:10px;align-items:center}
 #deadAirWorkspace .dead-file input{flex:1;min-width:0}#deadAirWorkspace .dead-check{display:flex;align-items:flex-start;gap:10px}
 #deadAirWorkspace input[type=checkbox]{width:auto;margin-top:5px}#deadAirWorkspace progress{width:100%;height:18px;accent-color:var(--green)}
 #deadAirWorkspace .dead-summary{font-size:18px;margin:18px 0;line-height:1.9}#deadAirStart{width:100%;margin-top:24px}
 #deadAirWorkspace .actions{display:flex;flex-wrap:wrap;gap:12px;margin-top:16px}#deadAirDownloads a{font-weight:bold}
 #deadAirWorkspace small{display:block;line-height:1.8;color:var(--muted)}#deadAirConfirm{max-width:500px}
 @media(max-width:750px){#deadAirWorkspace .dead-grid{grid-template-columns:1fr}}
 `;document.head.append(style);
 const host=document.createElement('main');host.id='deadAirWorkspace';host.innerHTML=`
 <div class="dead-grid"><section class="panel"><p class="pixel-kicker">LOCAL AUDIO / ZERO TOKENS</p><h2>ตัด Dead Air อีกครั้ง</h2>
 <p class="muted">ตัดเฉพาะช่วงเงียบจากเสียงแทร็ก 1 ไม่ถอดข้อความ ไม่เรียงเรื่องใหม่ ไม่เรียก API</p>
 <label for="deadAirXml">XML ต้นฉบับหรือ XML ที่ตัดแล้ว · 50 fps</label><div class="dead-file"><input id="deadAirXml" placeholder="เลือก XML ที่ต้องการกระชับ"><button id="deadAirPick" type="button">เลือกไฟล์</button></div>
 <div class="actions"><button id="deadAirInspect" type="button">ตรวจไฟล์ / เลือก Sequence</button></div>
 <label for="deadAirSequence">Sequence ที่จะตัด</label><select id="deadAirSequence"><option value="0">Sequence แรก · กดตรวจไฟล์หากมีหลาย Sequence</option></select>
 <label for="deadAirRelink">โฟลเดอร์สื่อ · กรณีหาไฟล์เดิมไม่พบ</label><div class="dead-file"><input id="deadAirRelink" placeholder="ไม่จำเป็นหากสื่ออยู่ตำแหน่งเดิม"><button id="deadAirFolder" type="button">เลือก</button></div>
 <label for="deadAirProfile">ความกระชับ</label><select id="deadAirProfile"><option value="gentle">ระมัดระวัง · เผื่อหัวท้ายคำมากกว่า</option><option value="balanced">สมดุล · ลดช่วงพักเพิ่ม</option><option value="tight">กระชับ · ควรฟังรอยต่อหลังตัด</option></select>
 <label class="dead-check"><input id="deadAirHeal" type="checkbox" checked><span>ลองคืนขอบเสียงที่อาจถูกตัดขาด<small>คืนได้สูงสุด 0.12 วินาที เมื่อสื่อเดียวกันและทุกแทร็กมีขอบตรงกัน ไม่สร้างคำหรือเดาคำพูด</small></span></label>
 <label class="dead-check"><input id="deadAirMerge" type="checkbox" checked><span>รวมคัทที่ต่อเนื่องจริง<small>เฉพาะคัทซ้ำซ้อนที่ไม่มีลิงก์ ไม่มีการเปลี่ยนเอฟเฟกต์หรือเทค ไม่ลบท่อนสั้นแบบเหมารวม</small></span></label>
 <button id="deadAirStart" class="primary" type="button">เริ่มตัดในเครื่อง · 0 token</button>
 <small>คงทุกแทร็กเสียงและลิงก์เดิม · ไม่มี crossfade / marker เพิ่ม · ส่งออกเป็นไฟล์ใหม่</small></section>
 <section class="panel"><p class="pixel-kicker">LOCAL RESULT</p><h2>ความคืบหน้า</h2><p id="deadAirStatus">พร้อมตรวจไฟล์</p>
 <progress id="deadAirProgress" max="100" value="0" aria-label="ความคืบหน้าการตัดในเครื่อง"></progress><div class="dead-summary"><span id="deadAirPercent">0%</span> · <span id="deadAirClock">00:00</span><small>OpenAI API: 0 คำขอ · 0 token · $0</small></div>
 <div class="actions"><button id="deadAirStop" type="button" hidden>หยุด</button><button id="deadAirOpen" type="button" hidden>เปิดโฟลเดอร์งาน</button></div>
 <div id="deadAirSummary"></div><div id="deadAirDownloads" class="actions"></div>
 <small>ตรวจได้จากเสียงและขอบสื่อ ไม่เข้าใจความหมายของคำ คำที่ไม่มีเสียงต้นฉบับให้คืนหรือคัทต่างเทคยังต้องฟังตรวจเอง</small>
 </section></div>`;
 document.querySelector('.content-mode-panel').after(host);
 const dialog=document.createElement('dialog');dialog.id='deadAirConfirm';dialog.innerHTML='<h2>พร้อมตัด Dead Air ไหม?</h2><p id="deadAirConfirmInfo"></p><p>ทำในเครื่อง · ไม่ใช้ API · เก็บไฟล์เดิมไว้</p><form method="dialog" class="actions"><button value="yes" class="primary">จัดไป</button><button value="no">ไม่เอาอะ</button></form>';document.body.append(dialog);
 function data(){return {xml:$('deadAirXml').value.trim(),sequence_index:Number($('deadAirSequence').value),relink:$('deadAirRelink').value.trim(),dead_air_profile:$('deadAirProfile').value,heal_edges:$('deadAirHeal').checked,merge_short:$('deadAirMerge').checked}}
 let inspection=null;
 async function inspectFile(){const result=await api('/api/dead-air-inspect',data());inspection=result;const selected=$('deadAirSequence').value;$('deadAirSequence').replaceChildren(...result.sequences.map(r=>new Option(r.name+' · '+minuteClock(r.seconds,true)+' · '+r.fps+' fps',String(r.index))));if(result.sequences.some(r=>String(r.index)===selected))$('deadAirSequence').value=selected;return result}
 $('deadAirInspect').onclick=()=>action(inspectFile);
 $('deadAirXml').addEventListener('input',()=>{inspection=null;$('deadAirSequence').replaceChildren(new Option('Sequence แรก · กดตรวจไฟล์','0'))});
 $('deadAirPick').onclick=()=>action(async()=>{const r=await api('/api/pick',{kind:'xml'});if(r.path){$('deadAirXml').value=r.path;inspection=null;await inspectFile()}});
 $('deadAirFolder').onclick=()=>action(async()=>{const r=await api('/api/pick',{kind:'folder'});if(r.path)$('deadAirRelink').value=r.path});
 $('deadAirStart').onclick=()=>action(async()=>{if($('deadAirStart').disabled)return;$('deadAirStart').disabled=true;try{await inspectFile();const c=data(),s=inspection.sequences[c.sequence_index];if(s.fps!==50)throw Error('เลือก Sequence 50 fps');$('deadAirConfirmInfo').textContent=s.name+' · '+minuteClock(s.seconds,true)+' · '+$('deadAirProfile').selectedOptions[0].textContent;dialog.returnValue='';dialog.showModal();const yes=await new Promise(resolve=>dialog.addEventListener('close',()=>resolve(dialog.returnValue==='yes'),{once:true}));if(!yes)return;const j=await api('/api/dead-air',c);await select(j.id)}finally{$('deadAirStart').disabled=!!$('run').disabled}});
 $('deadAirStop').onclick=()=>action(async()=>{if(current)await api('/api/stop',{id:current})});
 $('deadAirOpen').onclick=()=>action(async()=>{if(current)await api('/api/open-job-folder',{id:current})});
 const oldMode=updateContentMode;updateContentMode=function(){oldMode();document.body.dataset.contentMode=mode.value};mode.onchange=updateContentMode;
 const oldConfig=showConfig;showConfig=function(c){oldConfig(c);if(c?.content_mode==='dead_air'){$('deadAirXml').value=c.xml||'';$('deadAirRelink').value=c.relink||'';$('deadAirProfile').value=c.dead_air_profile||'gentle';$('deadAirHeal').checked=c.heal_edges!==false;$('deadAirMerge').checked=c.merge_short!==false;$('deadAirSequence').replaceChildren(new Option('Sequence '+(Number(c.sequence_index||0)+1),String(c.sequence_index||0)));updateContentMode()}};
 function draw(){const local=detail?.config?.content_mode==='dead_air'&&detail?.job?.id===current;const j=local?(uiLiveJob?.id===current?uiLiveJob:detail.job):null;const running=j?.status==='running';const percent=j?.status==='done'?100:Number(j?.percent||0);$('deadAirStatus').textContent=j?.message||'พร้อมตรวจไฟล์';$('deadAirPercent').textContent=percent+'%';$('deadAirProgress').value=percent;$('deadAirClock').textContent=minuteClock(running?Math.max(0,Date.now()/1000-j.started):local?detail.timings?.elapsed_seconds||0:0);$('deadAirStop').hidden=!running;$('deadAirOpen').hidden=!j;$('deadAirStart').disabled=!!$('run').disabled;
  for(const id of ['deadAirXml','deadAirRelink','deadAirSequence','deadAirProfile','deadAirHeal','deadAirMerge','deadAirPick','deadAirInspect','deadAirFolder'])$(id).disabled=!!$('run').disabled;
 }
 const oldResult=showResult;showResult=function(d){if(d?.config?.content_mode!=='dead_air')return oldResult(d);for(const id of ['results','editor','sourceCoverage','groupCheckpoint','transcriptCheckpoint','draftCheckpoint','superResults'])if($(id))$(id).hidden=true;$('deadAirDownloads').replaceChildren();$('deadAirSummary').replaceChildren();const r=d['dead-air-result'];if(r&&d.job?.status==='done'){const summary=document.createElement('p');summary.className='dead-summary';summary.textContent='เหลือ '+minuteClock(r.output_seconds,true)+' · ตัดออก '+r.removed_seconds.toFixed(2)+' วินาที · คืนขอบเสียง '+r.restored_gaps.length+' จุด · รวมคัทซ้ำซ้อน '+r.merged_redundant_cuts+' จุด';const note=document.createElement('small');note.textContent='คัทสั้นที่ยังเก็บไว้ '+r.remaining_short_ranges.length+' ช่วง · จุดที่คืนขอบไม่ได้ '+r.unresolved_edges.length+' จุด · ดูรายงานและฟังตรวจ';$('deadAirSummary').append(summary,note);const xml=link('ดาวน์โหลด XML',r.xml_file);xml.classList.add('primary','inwoum-download-ready');$('deadAirDownloads').append(xml,link('รายงานตรวจคัท',r.report_file))}draw()};
 const oldRefresh=refresh;refresh=async function(...args){await oldRefresh(...args);draw()};
 const oldClear=clearProjectView;clearProjectView=function(){oldClear();$('deadAirSummary').replaceChildren();$('deadAirDownloads').replaceChildren();draw()};
 setInterval(()=>{if(mode.value==='dead_air')draw()},1000);updateContentMode();
})();
