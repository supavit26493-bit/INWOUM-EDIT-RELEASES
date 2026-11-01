"""Apply current deterministic quality checks to a saved plan; no API calls."""
import argparse
import json
from pathlib import Path
import bootstrap
from engine import save
from optimized_editorial import validate_forbidden_fragments,validate_names,validate_speech


def audit(folder,mark_error=False):
    folder=Path(folder).resolve()
    if folder.parent!=(bootstrap.ROOT/'jobs').resolve():raise ValueError('เลือก job ในโปรแกรมเท่านั้น')
    load=lambda n:json.loads((folder/n).read_text('utf8'))
    problems=[];plan=load('plan.json');catalog=load('catalog.json')
    checks=[lambda:validate_forbidden_fragments(plan,catalog),lambda:validate_names(load('content-inventory.json'),catalog,plan)]
    if load('config.json').get('editorial_request_version')=='sparse-review-v4' or load('config.json').get('editorial_workflow')=='self-edit-v6':
        checks.append(lambda:validate_speech(plan,catalog))
    for check in checks:
        try:check()
        except ValueError as error:problems.append(str(error))
    result=dict(passed=not problems,problems=problems,network_calls=0,edit_choices_changed=False)
    save(folder/'quality-audit.json',result)
    if problems and mark_error:
        status=load('status.json')
        if status['status']=='running':raise ValueError('งานกำลังรัน ห้ามเปลี่ยนสถานะ')
        status.update(status='error',stage='quality_gate',message='ตรวจคุณภาพล่าสุด: '+problems[0])
        status['events']=(status.get('events',[])+[status['message']])[-60:]
        save(folder/'status.json',status)
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('job');parser.add_argument('--mark-error',action='store_true')
    args=parser.parse_args();print(json.dumps(audit(bootstrap.ROOT/'jobs'/args.job,args.mark_error),ensure_ascii=False,indent=2))
