"""Objective evidence only; proposals and warnings never become human approval."""
import re
from fast_logic import merge,subtract

def coverage(duration,used,exclusions=()):
    domain=[(0,float(duration))]
    def checked(spans):
        for a,b in spans:
            if not 0<=a<b<=duration:raise ValueError('ช่วงเวลานอกต้นฉบับ')
        return merge(spans)
    used=checked(list(used));approved=[];pending=[]
    for row in exclusions:
        if not row.get('reason','').strip():raise ValueError('ข้อยกเว้นต้องมีเหตุผล')
        (approved if row.get('confirmed_by_user') is True else pending).append((row['start'],row['end']))
    approved=checked(approved);checked(pending)
    eligible=subtract(domain,approved);missing=subtract(eligible,used)
    seconds=lambda spans:sum(b-a for a,b in spans)
    eligible_seconds=seconds(eligible);used_eligible=eligible_seconds-seconds(missing)
    return dict(source_seconds=round(duration,2),unique_used_seconds=round(seconds(used),2),
        raw_used_percent=round(100*seconds(used)/duration,2) if duration else 0,
        confirmed_excluded_seconds=round(seconds(approved),2),pending_exclusion_seconds=round(seconds(checked(pending)),2),
        eligible_seconds=round(eligible_seconds,2),eligible_used_seconds=round(used_eligible,2),
        eligible_unused_seconds=round(seconds(missing),2),eligible_used_percent=round(100*used_eligible/eligible_seconds,2) if eligible_seconds else None,
        usage_target=None,
        unused_eligible_ranges=missing,method='source union; only user-confirmed exclusions removed; semantic and listening review separate')

def price_findings(text):
    # Mask only approved banknote expressions for numeric price matching, never speech.
    masked=re.sub(r'(?:\d+|[หนึ่งสองสามสี่ห้าหกเจ็ดแปดเก้าสิบ]+)\s*ใบ\s*(?:แดง|เทา)','',text)
    findings=[]
    for m in re.finditer(r'บาท|หลัก\s*(?:ร้อย|พัน|หมื่น|แสน|ล้าน)|(?:ราคา|เงิน|งบ)\s*(?:\d[\d,]*(?:\.\d+)?|(?:หนึ่ง|สอง|สาม|สี่|ห้า|หก|เจ็ด|แปด|เก้า)(?:ร้อย|พัน|หมื่น))',masked):
        if re.match(r'\s*(?:กรัม|กิโลกรัม|วัตต์|โวลต์|องศา|นาที|วินาที|เมตร)',masked[m.end():]):continue
        findings.append(dict(issue='explicit_price_needs_source_review',text=m.group()))
    return findings

def cut_findings(ranges):
    result=[];cursor=0;starts=[]
    for i,r in enumerate(ranges):
        seconds=r['end']-r['start'];starts.append(cursor);cursor+=seconds
        if seconds<.24:result.append(dict(issue='very_short_source_piece',range_index=i,seconds=round(seconds,2)))
    for i in range(len(starts)-3):
        if starts[i+3]-starts[i]<1.5:result.append(dict(issue='dense_cut_cluster',range_indices=list(range(i,i+4))))
    return result
