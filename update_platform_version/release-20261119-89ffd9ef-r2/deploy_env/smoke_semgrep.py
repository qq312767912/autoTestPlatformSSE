import sys
sys.path.insert(0, '/app')
from app import ScanRequest, scan

result = scan(ScanRequest(files=[{'path': 'probe.py', 'content': 'import subprocess\nsubprocess.run(input(), shell=True)\n'}]))
assert result.get('scanner_exit_code') in (0, 1), result
assert result.get('results') and not result.get('errors'), result
print('Semgrep native scan passed, findings:', len(result['results']))
