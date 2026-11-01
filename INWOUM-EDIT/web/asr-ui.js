// Display saved comparison results only; the test launch panel was removed.
function showAsrComparison(d) {
    let panel = $('asrComparison');
    if (!panel) {
        panel = document.createElement('section'); panel.id = 'asrComparison'; panel.className = 'panel';
        document.querySelector('article').prepend(panel);
    }
    panel.replaceChildren();
    const result = d?.['asr-comparison'];
    panel.hidden = !result;
    if (!result) return;
    const h = document.createElement('h2'); h.textContent = 'ผลเทียบ Whisper กับ API';
    const summary = document.createElement('p');
    summary.textContent = result.model + ' · ส่งเสียงแล้ว ' + result.uploaded_seconds + '/' + result.total_seconds + ' วินาที · ' + result.api_calls + ' คำขอ';
    const note = document.createElement('p'); note.className = 'muted';
    note.textContent = 'ฟังเสียงชุดเดียวกันแล้วเทียบข้อความ ไม่มี transcript ที่คนยืนยัน จึงยังไม่รายงานเปอร์เซ็นต์ความแม่นยำ และยังไม่นำข้อความ API ไปผูกกับเวลาคัทเก่า';
    panel.append(h, summary, note);
    for (const row of result.rows) {
        const block = document.createElement('div'); block.className = 'version';
        const heading = document.createElement('h3'); heading.textContent = 'ต้นฉบับ ' + time(row.start) + '–' + time(row.end);
        const audio = document.createElement('audio'); audio.controls = true; audio.preload = 'none'; audio.src = file(row.audio_file);
        const grid = document.createElement('div'); grid.className = 'grid';
        for (const [name, text, elapsed] of [['Whisper · temperature=0', row.whisper, row.whisper_seconds], [result.model, row.api || 'ยังไม่ได้ผล API', row.api_seconds]]) {
            const cell = document.createElement('div');
            const label = document.createElement('h3'); label.textContent = name + (elapsed === undefined ? '' : ' · ' + elapsed + 's');
            const body = document.createElement('p'); body.textContent = text; body.style.whiteSpace = 'pre-wrap';
            cell.append(label, body); grid.append(cell);
        }
        block.append(heading, audio, grid);
        if (row.comparison) {
            const details = document.createElement('details');
            const summary = document.createElement('summary'); summary.textContent = 'ดูจุดที่ข้อความต่างกัน (ไม่ใช่คะแนนความถูกต้อง)';
            details.append(summary);
            for (const difference of row.comparison.differences) {
                const p = document.createElement('p'); p.textContent = 'Whisper: ' + (difference.whisper || '∅') + ' → API: ' + (difference.api || '∅');
                details.append(p);
            }
            block.append(details);
        }
        panel.append(block);
    }
    if (result.completed) panel.append(link('ดาวน์โหลดรายงานเปรียบเทียบ', 'ASR-COMPARISON.md'));
}
if (detail) showAsrComparison(detail);
