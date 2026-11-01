"""Whitelist packaging: never bundle environment, cache, model, keys or customer jobs."""
import hashlib
import json
from pathlib import Path
import zipfile
from runtime_contract import VERSION

root = Path(__file__).resolve().parent
target = root.parent / f'AI-EDIT-FAST-{VERSION}.zip'
files = []
for pattern in ('*.py', '*.cmd', '*.ps1', 'requirements*.txt', 'README.md', 'TEST-RESULTS.md', 'HANDOFF-NEW-PC.md', 'CONVERSATION-CONTEXT.md', '.gitignore', 'preview.png',
                'THIRD-PARTY.md', 'CAPCUT-INTEGRATION.md', 'ACCEPTED-REFERENCE.md', 'INSTALL-MANIFEST.json', 'RELEASE-*.md', 'capcut_tts_api/*.py', 'core/*.py', 'web/*.html', 'web/*.js', 'web/fonts/*.ttf', 'web/fonts/*.txt', 'assets/*.xml', 'SUPER-STYLE-STANDARD.md', 'tests/*.py', 'rules/*.md', 'rules/*.txt'):
    files.extend(root.glob(pattern))
files = sorted(set(files))
with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as archive:
    for file in files:
        archive.write(file, 'AI-EDIT-FAST/' + file.relative_to(root).as_posix())
print(json.dumps(dict(path=str(target), files=len(files), bytes=target.stat().st_size,
                     sha256=hashlib.sha256(target.read_bytes()).hexdigest()), indent=2))
