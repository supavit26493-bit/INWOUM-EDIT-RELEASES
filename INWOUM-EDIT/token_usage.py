"""Read-only totals of reported OpenAI text-planning usage for one job."""
import json,hashlib,re
from decimal import Decimal

PRICING_DATE='2026-10-24'
PRICING_SOURCE='https://developers.openai.com/api/docs/pricing'
# USD per million text tokens, Standard processing. Cache writes replace ordinary input.
PRICING_DATES={'gpt-6.1-terra':'2026-10-29'}
RATES={'gpt-6.1-terra':('1','0.05','5'),'gpt-6.1-sol':('2','0.10','10'),'gpt-6-luna':('0.10','0.01','0.50')}

def estimate(row,incoming,outgoing):
    model=row.get('model');request=row.get('_pricing_request') or {}
    base=next((m for m in RATES if model==m or isinstance(model,str) and re.fullmatch(re.escape(m)+r'-\d{4}-\d{2}-\d{2}',model)),None)
    if not base:return dict(usd=None,reason='ไม่ทราบราคาหรือโมเดลของคำขอนี้')
    if incoming is None or outgoing is None:return dict(usd=None,reason='usage ไม่ครบ')
    tier=row.get('service_tier',request.get('service_tier','auto'))
    if tier not in ('auto','default','standard',None):return dict(usd=None,reason='ยังไม่รองรับราคาของ service tier นี้')
    if request.get('tools'):return dict(usd=None,reason='คำขอมีเครื่องมือที่อาจมีค่าบริการเพิ่ม')
    details=(row.get('usage') or {}).get('input_tokens_details') or {}
    cached=number(details.get('cached_tokens'));writes=number(details.get('cache_write_tokens'))
    assumptions=[]
    if cached is None:cached=0;assumptions.append('ไม่มี cached usage: คิด Input ปกติ')
    if writes is None:writes=0;assumptions.append('ไม่มี cache-write usage: คิด Input ปกติ')
    if cached+writes>incoming:return dict(usd=None,reason='จำนวน cache token ไม่สอดคล้องกับ Input')
    ttl=(request.get('prompt_cache_options') or {}).get('ttl','30m')
    # Per-breakpoint TTLs require a per-lifetime usage breakdown; do not guess.
    if writes and ('prompt_cache_breakpoint' in json.dumps(request) or ttl not in ('30m','12h')):
        return dict(usd=None,reason='ไม่ทราบอัตรา cache write ที่แน่นอน')
    uncached_rate,cached_rate,output_rate=map(Decimal,RATES[base])
    if base=='gpt-6.1-terra' and incoming>272000:
        uncached_rate*=2;cached_rate*=2;output_rate*=Decimal('1.5')
        assumptions.append('Terra เกิน 272K input: ใช้อัตรา long context ทั้งคำขอ')
    write_rate=uncached_rate*(Decimal('2.5') if ttl=='12h' else Decimal('1.25'))
    parts=dict(input_usd=Decimal(incoming-cached-writes)*uncached_rate/1000000,
               cached_usd=Decimal(cached)*cached_rate/1000000,
               cache_write_usd=Decimal(writes)*write_rate/1000000,
               output_usd=Decimal(outgoing)*output_rate/1000000)
    return dict(usd=float(sum(parts.values())),**{k:float(v) for k,v in parts.items()},
                assumptions=assumptions,model=model,cache_write_tokens=writes,pricing_date=PRICING_DATES.get(base,PRICING_DATE))

def number(value):
    return value if type(value) is int and value>=0 else None

def summarize(rows):
    totals=dict(input_tokens=0,output_tokens=0,total_tokens=0,cached_input_tokens=0,reasoning_tokens=0)
    known=0;unknown=0;partial=False;seen=set();calls=[]
    fields={key:0 for key in ('input_tokens','output_tokens','total_tokens')}
    for row in rows:
        # Request hashes are not deduplication keys: explicitly retried requests cost again.
        identity=row.get('attempt_id') or row.get('response_id')
        if identity and identity in seen:continue
        if identity:seen.add(identity)
        usage=row.get('usage')
        if not isinstance(usage,dict):usage={}
        incoming=number(usage.get('input_tokens',usage.get('prompt_tokens')))
        outgoing=number(usage.get('output_tokens',usage.get('completion_tokens')))
        total=number(usage.get('total_tokens'))
        if total is None and incoming is not None and outgoing is not None:total=incoming+outgoing
        available=any(v is not None for v in (incoming,outgoing,total))
        if available:
            known+=1
            for key,value in [('input_tokens',incoming),('output_tokens',outgoing),('total_tokens',total)]:
                if value is not None:totals[key]+=value;fields[key]+=1
                else:partial=True
            cached=number((usage.get('input_tokens_details') or {}).get('cached_tokens'))
            reasoning=number((usage.get('output_tokens_details') or {}).get('reasoning_tokens'))
            if cached is not None:totals['cached_input_tokens']+=cached
            if reasoning is not None:totals['reasoning_tokens']+=reasoning
        else:unknown+=1
        calls.append(dict(stage=row.get('stage','API'),status=row.get('status','unknown'),
                          input_tokens=incoming,output_tokens=outgoing,total_tokens=total,
                          model=row.get('model'),effort=row.get('_request_effort'),
                          cost=estimate(row,incoming,outgoing)))
    priced=[c['cost'] for c in calls if c['cost']['usd'] is not None]
    cost=dict(usd=sum(c['usd'] for c in priced) if priced or not calls else None,
              priced_calls=len(priced),unpriced_calls=len(calls)-len(priced),partial=len(priced)!=len(calls),
              assumptions=sorted({a for c in priced for a in c.get('assumptions',[])}),
              pricing_date=' / '.join(sorted({c.get('pricing_date',PRICING_DATE) for c in priced})) or PRICING_DATE,source=PRICING_SOURCE,basis='Standard · USD · เฉพาะการเรียบเรียง · ไม่รวมภาษี/ส่วนลดบัญชี')
    return dict(**totals,field_reported_calls=fields,reported_calls=known,unreported_calls=unknown,total_calls=len(calls),
                partial=partial or unknown>0,calls=calls,cost=cost,scope='current_job_openai_text_requests',
                models_used=[dict(model=m,effort=e) for m,e in dict.fromkeys((c['model'],c['effort']) for c in calls if c.get('model'))])

def read(folder):
    path=folder/'api-usage.json'
    rows=json.loads(path.read_text('utf8')) if path.exists() else []
    if not isinstance(rows,list):raise ValueError('รูปแบบ usage ไม่ถูกต้อง')
    # Only use model metadata from the exact saved request, never today's UI selection.
    enriched=[]
    for row in rows:
        item=dict(row);stage=row.get('stage','')
        if isinstance(stage,str) and re.fullmatch(r'[A-Za-z0-9_-]+',stage):
            request_file=folder/(stage+'-request.json')
            try:
                request=json.loads(request_file.read_text('utf8'))
                digest=hashlib.sha256(json.dumps(request,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
                if digest==row.get('request_hash'):
                    item['model']=request.get('model');item['_pricing_request']=request
                    item['_request_effort']=request.get('reasoning',{}).get('effort','default')
            except (OSError,ValueError):pass
        enriched.append(item)
    return summarize(enriched)
