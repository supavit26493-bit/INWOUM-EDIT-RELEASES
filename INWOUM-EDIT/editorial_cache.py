"""Reuse completed v3 stages when adding the v4 polish pass on identical source/config."""
import json
import shutil


def reuse_upgrade(source,folder,config):
    prior=json.loads((source/'config.json').read_text(encoding='utf8'))
    if prior.get('editorial_workflow')!='self-edit-v3' or config.get('editorial_workflow')!='self-edit-v4':return 0
    keys=('xml','relink','audio_track','profile','model','effort','notes','protected','editorial_policy','silence_policy','phrase_granularity','editorial_request_version')
    if any(prior.get(k)!=config.get(k) for k in keys):return 0
    for name in ('audio-identity.json','source-sha256.json'):
        if not (source/name).is_file() or not (folder/name).is_file() or (source/name).read_bytes()!=(folder/name).read_bytes():return 0
    if not (source/'self-final-response.json').is_file() or not (source/'full-coverage.json').is_file():return 0
    copied=[]
    for stage in ('self-inventory','self-draft','self-recovery','self-final'):
        for suffix in ('-response.json','-repair-response.json','-validation-error.json'):
            name=stage+suffix
            if (source/name).is_file():shutil.copy2(source/name,folder/name);copied.append(name)
    ledger=source/'api-usage.json'
    if ledger.is_file():shutil.copy2(ledger,folder/ledger.name)
    attempts=len(json.loads(ledger.read_text(encoding='utf8'))) if ledger.is_file() else 0
    (folder/'editorial-cache-reuse.json').write_text(json.dumps(dict(source_job=source.name,inherited_attempts=attempts,
        files=copied,notes='Reused completed stage responses; unknown attempts retained, never resubmitted. Each request hash is rechecked by the workflow.'),ensure_ascii=False,indent=2),encoding='utf8')
    return len(copied)
