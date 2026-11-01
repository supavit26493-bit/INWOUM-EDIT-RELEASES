"""Reserve and complete independent API calls without losing shared ledger rows."""
import json
import copy
import threading
import time
import uuid
from planner import fingerprint,plan_once

LOCK=threading.RLock()


def semantic_fingerprint(payload):
    normalized=copy.deepcopy(payload)
    try:normalized['input']=json.dumps(json.loads(payload['input']),ensure_ascii=False,sort_keys=True,separators=(',',':'))
    except (ValueError,TypeError,KeyError):pass
    return fingerprint(normalized)


def call(payload,folder,key,stage,max_calls=20):
    from engine import save
    from model_catalog import prepare_request
    payload=prepare_request(payload)
    import api_budget
    # Force Standard pricing for new budgeted requests. Completed legacy caches
    # retain their original request exactly and incur no generation charge.
    if api_budget.enabled(folder):
        complete=folder/(stage+'-response.json');saved_request=folder/(stage+'-request.json')
        if not complete.exists() or saved_request.exists() and json.loads(saved_request.read_text('utf8')).get('service_tier')=='default':
            payload['service_tier']='default'
        if not complete.exists():payload['prompt_cache_options']={'ttl':'30m'}
        elif saved_request.exists():
            saved_options=json.loads(saved_request.read_text('utf8')).get('prompt_cache_options')
            if saved_options is not None:payload['prompt_cache_options']=saved_options
    digest=fingerprint(payload);semantic=semantic_fingerprint(payload)
    response_file=folder/(stage+'-response.json');request_file=folder/(stage+'-request.json');ledger_file=folder/'api-usage.json'
    with LOCK:
        if response_file.exists():
            saved=json.loads(response_file.read_text('utf8'))
            if saved['request_hash']!=digest:
                if not request_file.exists():raise ValueError('คำขอเปลี่ยน กรุณาสร้างงานใหม่')
                original=json.loads(request_file.read_text('utf8'))
                if fingerprint(original)!=saved['request_hash'] or semantic_fingerprint(original)!=semantic:raise ValueError('คำขอเปลี่ยน กรุณาสร้างงานใหม่')
                save(folder/(stage+'-cache-equivalence.json'),dict(original_request_hash=saved['request_hash'],current_request_hash=digest,semantic_hash=semantic,reason='JSON object key order only; arrays and all values preserved'))
            elif not request_file.exists():save(request_file,payload)
            return saved['response']
        ledger=json.loads(ledger_file.read_text('utf8')) if ledger_file.exists() else []
        prior=[r for r in ledger if r['request_hash']==digest or r.get('semantic_hash')==semantic or r['stage']==stage and r['status']=='sent']
        if prior and not (prior[-1]['status']=='sent' and prior[-1].get('retry_authorized_at')):
            raise ValueError('คำขอนี้ไม่ทราบผล ไม่ส่งซ้ำอัตโนมัติ')
        # Each planned 2.1 clip has its own bounded call. Do not spend the separate
        # correction allowance on those calls; old jobs without these stages are unchanged.
        counted=ledger if stage.startswith('approved-map-clip-') else [r for r in ledger if not r['stage'].startswith('approved-map-clip-')]
        if len(counted)>=max_calls:raise ValueError('ครบขีดจำกัดคำขอ เก็บผลที่ทำเสร็จไว้แล้ว')
        if not key.strip():raise ValueError('ใส่ OpenAI API key ก่อน')
        reservation=api_budget.reserve(payload,folder,key,ledger)
        attempt=uuid.uuid4().hex;started=time.time()
        ledger.append(dict(attempt_id=attempt,stage=stage,request_hash=digest,semantic_hash=semantic,status='sent',usage=None,
            started_at=started,input_characters=len(payload.get('input',''))))
        if reservation:ledger[-1].update(reservation)
        save(ledger_file,ledger)
        save(request_file,payload)
    raw=None;record=None
    try:
        raw,record=plan_once(payload,key)
        if raw is None and record and record.get('incomplete_reason')=='max_output_tokens' and stage.startswith('approved-map-'):
            record['error']='คำตอบขั้นประกอบบทชนเพดาน · เก็บแผนและข้อความเดิมไว้แล้ว · อย่าวางแผนหรือถอดเสียงซ้ำ · ตรวจงบก่อนทดสอบประกอบบทใหม่'
    finally:
        with LOCK:
            ledger=json.loads(ledger_file.read_text('utf8'))
            row=next(r for r in ledger if r.get('attempt_id')==attempt)
            row['elapsed_seconds']=round(time.time()-started,2)
            if record is not None:row.update(record,status='completed' if raw is not None else 'failed')
            save(ledger_file,ledger)
            if raw is not None:save(response_file,dict(request_hash=digest,response=raw))
    if raw is None:raise RuntimeError((record or {}).get('error','API ไม่คืนคำตอบ'))
    return raw
