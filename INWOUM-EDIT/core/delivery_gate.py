"""Require explicit per-cut and whole-edit review tied to exact built artifacts.

This records evidence; it cannot listen, judge Thai, or certify itself.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

RANGE_CHECKS = ('repeated_words', 'repeated_meaning', 'fillers', 'internal_pauses',
                'off_camera_speech', 'audio_edges')
JOIN_CHECKS = ('repetition_and_meaning', 'join_pause', 'audio_continuity')
EDIT_CHECKS = ('story_and_hook', 'risk_and_money', 'full_playthrough')
AUDIO_CHECKS = {'audio_edges', 'audio_continuity', 'full_playthrough'}


def binding(folder, manifest):
    names = ['manifest.json', manifest['package_name']]
    for edit in manifest['edits']:
        names.extend((edit['id']+'.xml', edit['id']+'.wav'))
    result = {}
    for name in names:
        path = (folder / name).resolve()
        if path.parent != folder.resolve():
            raise ValueError('Review artifacts must be direct children of the output folder')
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024*1024), b''):
                digest.update(chunk)
        result[name] = digest.hexdigest()
    return result


def checks(names):
    return {name: dict(status='pending', method='', reviewer='', evidence='') for name in names}


def template(manifest, artifacts):
    edits = []
    for edit in manifest['edits']:
        ranges = []
        for index, unit in enumerate(edit['ranges'], 1):
            ranges.append(dict(index=index, media=unit['media'],
                               start_frame=round(unit['start']*50),
                               end_frame=round(unit['end']*50),
                               checks=checks(RANGE_CHECKS),
                               retained_pause_explanations=[]))
        edits.append(dict(id=edit['id'], ranges=ranges,
                          joins=[dict(after_range=i, checks=checks(JOIN_CHECKS))
                                 for i in range(1, len(ranges))],
                          checks=checks(EDIT_CHECKS),
                          full_playthrough_coverage_seconds=None))
    return dict(schema_version=1, artifact_sha256=artifacts, edits=edits,
                note='Pending by default. ASR, VAD and waveform inspection cannot establish listening approval. Do not fill pass without actual review.')


def validate_review(manifest, artifacts, review):
    if review.get('schema_version') != 1:
        raise ValueError('Unsupported review schema')
    if review.get('artifact_sha256') != artifacts:
        raise ValueError('Artifacts changed: review the new edit; old review is stale')
    expected = template(manifest, artifacts)
    observed = review.get('edits', [])
    if [e.get('id') for e in observed] != [e['id'] for e in expected['edits']]:
        raise ValueError('Review does not cover every edit in order')
    problems = []

    def inspect_checks(actual, names, label):
        for name in names:
            entry = actual.get(name, {})
            if (entry.get('status') != 'pass' or
                not all(isinstance(entry.get(k), str) and entry[k].strip()
                        for k in ('method', 'reviewer', 'evidence'))):
                problems.append(label+': '+name+' needs review evidence')
            elif name in AUDIO_CHECKS and entry['method'] not in ('direct_audio', 'user_audio_review'):
                problems.append(label+': '+name+' requires actual audio review')

    for actual, reference, edit in zip(observed, expected['edits'], manifest['edits']):
        token = edit['id']
        units = actual.get('ranges', [])
        if len(units) != len(reference['ranges']):
            problems.append(token+': missing range reviews')
        for unit, ref in zip(units, reference['ranges']):
            if any(unit.get(k) != ref[k] for k in ('index', 'media', 'start_frame', 'end_frame')):
                problems.append(token+': range identity changed')
            inspect_checks(unit.get('checks', {}), RANGE_CHECKS, token+' range '+str(ref['index']))
            for pause in unit.get('retained_pause_explanations', []):
                if (not isinstance(pause, dict) or
                    not isinstance(pause.get('reason'), str) or not pause['reason'].strip() or
                    not isinstance(pause.get('source_start'), (int, float)) or
                    not isinstance(pause.get('source_end'), (int, float)) or
                    not ref['start_frame']/50 <= pause['source_start'] < pause['source_end'] <= ref['end_frame']/50):
                    problems.append(token+': retained pause needs bounded times and a concrete reason')
        joins = actual.get('joins', [])
        if [j.get('after_range') for j in joins] != [j['after_range'] for j in reference['joins']]:
            problems.append(token+': missing join reviews')
        for join in joins:
            inspect_checks(join.get('checks', {}), JOIN_CHECKS, token+' join '+str(join.get('after_range')))
        inspect_checks(actual.get('checks', {}), EDIT_CHECKS, token)
        coverage = actual.get('full_playthrough_coverage_seconds')
        if coverage != [0, edit['seconds']]:
            problems.append(token+': whole assembled edit has not been reviewed from start to end')
    if problems:
        raise ValueError('NOT READY: '+str(len(problems))+' pending/invalid checks\n'+'\n'.join(problems[:12]))


def initialize(folder):
    folder = Path(folder)
    manifest = json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
    artifacts = binding(folder, manifest)
    path = folder/'editorial-review.json'
    if path.exists():
        raise FileExistsError('Review already exists; preserve it and create a new output revision after recutting')
    path.write_text(json.dumps(template(manifest, artifacts), ensure_ascii=False, indent=2), encoding='utf-8')
    (folder/'RELEASE-STATUS.json').write_text(json.dumps(dict(status='not_ready',
        reason='Per-range, per-join and whole-edit editorial/audio review pending',
        artifact_sha256=artifacts), indent=2), encoding='utf-8')
    return path


def check(folder):
    folder = Path(folder)
    # Invalidate a previous pass before checking the current evidence.
    status = dict(status='not_ready')
    try:
        manifest = json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
        artifacts = binding(folder, manifest)
        status['artifact_sha256'] = artifacts
        review = json.loads((folder/'editorial-review.json').read_text(encoding='utf-8'))
        validate_review(manifest, artifacts, review)
        script = Path(__file__).resolve().with_name('check_project_drafts.py')
        result = subprocess.run([sys.executable, '-X', 'utf8', str(script), str(folder), '--structure-only'],
                                capture_output=True, text=True, encoding='utf-8')
        if result.returncode:
            raise ValueError('Technical checks failed: '+result.stdout+result.stderr)
        status.update(status='review_complete', technical_output=result.stdout,
                      note='Recorded review and structure checks complete; this is not a guarantee of zero errors.')
    except (ValueError, FileNotFoundError, KeyError, TypeError) as error:
        status['reason'] = str(error)
        raise ValueError(str(error)) from error
    finally:
        (folder/'RELEASE-STATUS.json').write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('folder')
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    try:
        if args.check:
            check(args.folder)
            print('REVIEW COMPLETE: editorial evidence and XML structure checks passed')
        else:
            print('NOT READY: review required:', initialize(args.folder))
    except (ValueError, FileExistsError, FileNotFoundError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
