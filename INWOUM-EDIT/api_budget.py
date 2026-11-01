"""Fail-closed USD reservations for 2.1 jobs, including unknown/failed requests."""
import json,urllib.request,urllib.error
from decimal import Decimal
from planner import fingerprint
from token_usage import RATES,estimate
LIMIT=Decimal('0.50')

def enabled(folder):
    p=folder/'config.json'
    if not p.exists():return False
    c=json.loads(p.read_text('utf8'))
    return c.get('content_mode','sales')=='sales' and c.get('sales_style')in ('sales_2_1','sales_2_2')

def count_input(payload,key):
    allowed=('model','input','instructions','text','reasoning','tools','tool_choice','parallel_tool_calls')
    body={k:payload[k] for k in allowed if k in payload}
    req=urllib.request.Request('https://api.openai.com/v1/responses/input_tokens',
        data=json.dumps(body,ensure_ascii=False).encode(),method='POST',
        headers={'Authorization':'Bearer '+key.strip(),'Content-Type':'application/json'})
    try:
        with urllib.request.urlopen(req,timeout=60) as response:result=json.load(response)
    except (urllib.error.URLError,TimeoutError,ValueError):
        raise ValueError('ตรวจจำนวน token ก่อนส่งไม่สำเร็จ จึงไม่เรียก API สร้างคำตอบเพื่อรักษางบ $0.50') from None
    value=result.get('input_tokens')
    if type(value)!=int or value<0:raise ValueError('ไม่ทราบจำนวน Input token จึงหยุดเพื่อรักษางบ')
    return value

def reserve(payload,folder,key,ledger):
    from engine import save
    if not enabled(folder):return None
    model=payload.get('model')
    if model not in RATES or payload.get('tools') or payload.get('service_tier','auto') not in ('auto','default','standard'):
        raise ValueError('ไม่รองรับราคาของคำขอนี้ จึงหยุดเพื่อรักษางบ $0.50')
    spent=Decimal(0)
    for row in ledger:
        path=folder/(row['stage']+'-request.json')
        request=json.loads(path.read_text('utf8')) if path.exists() else {}
        usage=row.get('usage') or {}
        actual=estimate(dict(row,model=request.get('model'),_pricing_request=request),usage.get('input_tokens'),usage.get('output_tokens'))
        value=actual.get('usd')
        if value is not None:spent+=Decimal(str(value))
        elif row.get('budget_reservation_usd') is not None:spent+=Decimal(str(row['budget_reservation_usd']))
        else:raise ValueError('มีคำขอเดิมที่ไม่ทราบค่าใช้จ่าย จึงหยุดเพื่อรักษางบ $0.50')
    # Leave a 2-cent margin below the requested cap; never assume cache discounts.
    remaining=LIMIT-Decimal('0.02')-spent
    if remaining<=0:raise ValueError('ถึงงบสูงสุด $0.50 แล้ว เก็บผลที่ทำเสร็จไว้ ไม่เรียก API ต่อ')
    cache=folder/'budget-token-counts.json';counts=json.loads(cache.read_text('utf8')) if cache.exists() else {}
    digest=fingerprint(payload)
    incoming=counts.get(digest)
    if type(incoming)!=int:incoming=count_input(payload,key);counts[digest]=incoming;save(cache,counts)
    if incoming>100000:raise ValueError('Input ใหญ่เกินขอบเขตราคาที่ตรวจยืนยัน จึงหยุดเพื่อรักษางบ $0.50')
    # Reserve the most expensive supported cache-write lifetime, plus long-context uplift.
    short_cache=(payload.get('prompt_cache_options') or {}).get('ttl')=='30m' and 'prompt_cache_breakpoint' not in json.dumps(payload.get('input'))
    rate=Decimal(RATES[model][0])*(Decimal('1.25') if short_cache else Decimal('2.5'));out=Decimal(RATES[model][2])
    if model=='gpt-6.1-terra' and incoming>272000:rate*=2;out*=Decimal('1.5')
    input_cost=Decimal(incoming)*rate/1000000
    maximum=payload.get('max_output_tokens')
    if type(maximum)!=int or maximum<1:raise ValueError('ไม่มีเพดาน Output จึงหยุดเพื่อรักษางบ')
    worst=input_cost+Decimal(maximum)*out/1000000
    # Do not truncate an approved request to squeeze it into the remaining budget.
    if worst>remaining:
        save(folder/'budget-stop.json',dict(limit_usd=float(LIMIT),spent_usd=float(spent),next_max_usd=float(worst),remaining_usd=float(remaining)))
        raise ValueError('งบที่เหลือไม่พอสำหรับคำขอถัดไปภายใน $0.50 · เก็บผลที่ทำเสร็จไว้แล้ว')
    return dict(budget_reservation_usd=float(worst),budget_input_tokens=incoming,budget_limit_usd=float(LIMIT))
