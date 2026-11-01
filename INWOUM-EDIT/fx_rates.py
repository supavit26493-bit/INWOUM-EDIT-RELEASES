"""Reference-only USD/THB conversion; no OpenAI requests, no change to USD budgets."""
import json,time,threading,urllib.request,xml.etree.ElementTree as ET
from pathlib import Path
from decimal import Decimal,InvalidOperation
ROOT=Path(__file__).parent
URL='https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml'
SOURCE='https://www.ecb.europa.eu/stats/policy_and_exchange_rates/euro_reference_exchange_rates/html/index.en.html'
_quote=None;_loaded=0;_lock=threading.Lock()

def normalize(raw):
    date=raw['date'];time.strptime(date,'%Y-%m-%d')
    rate=Decimal(str(raw['thb_per_eur']))/Decimal(str(raw['usd_per_eur']))
    if not rate.is_finite() or not Decimal('1')<rate<Decimal('200'):raise ValueError('Invalid exchange rate')
    return dict(date=date,thb_per_usd=str(rate),source=SOURCE,reference_only=True)

def fetch():
    req=urllib.request.Request(URL,headers={'User-Agent':'INWOUM-EDIT/0.15.19'})
    with urllib.request.urlopen(req,timeout=3) as response:tree=ET.fromstring(response.read(250000))
    dated=next(node for node in tree.iter() if node.attrib.get('time'))
    rates={node.attrib['currency']:node.attrib['rate'] for node in dated if 'currency' in node.attrib}
    return normalize(dict(date=dated.attrib['time'],usd_per_eur=rates['USD'],thb_per_eur=rates['THB']))

def quote():
    global _quote,_loaded
    with _lock:
        if _quote is not None and time.time()-_loaded<86400:return dict(_quote)
        try:result=fetch();result['cached_reference']=False
        except Exception:
            if _quote is not None:result=dict(_quote)
            else:result=normalize(json.loads((ROOT/'fx-reference.json').read_text('utf8')))
            result['cached_reference']=True
        _quote=result;_loaded=time.time();return dict(result)

def attach(data,folder):
    """Freeze one reference per job; repeated UI polling must not rewrite the rate."""
    cost=data.get('cost',{});usd=cost.get('usd');path=folder/'fx-reference.json'
    if usd is None:return data
    if not data.get('total_calls',0):cost['thb']=0.0;return data
    try:
        rate=json.loads(path.read_text('utf8')) if path.exists() else quote()
        value=Decimal(str(rate['thb_per_usd']))
        if not value.is_finite() or not Decimal('1')<value<Decimal('200'):raise ValueError('Invalid rate')
        if not path.exists() and data.get('total_calls',0):
            from engine import save
            save(path,rate)
        cost['thb']=float(Decimal(str(usd))*value);cost['fx']=rate
    except (OSError,ValueError,KeyError,InvalidOperation,ZeroDivisionError):
        cost['thb']=None;cost['fx_note']='ไม่มีอัตราอ้างอิงที่ใช้ได้ ยังคงแสดง USD'
    return data
