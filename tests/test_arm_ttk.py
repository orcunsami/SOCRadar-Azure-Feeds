#!/usr/bin/env python3
"""Run every ARM-TTK check on the published template tree without exclusions."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import json

ROOT = Path(__file__).resolve().parents[1]
pwsh = shutil.which('pwsh')
if not pwsh:
    if os.environ.get('CI'):
        sys.exit('FAIL: PowerShell is required in CI')
    print('SKIP ARM-TTK: install PowerShell to run the template checks')
    sys.exit(0)

# CI supplies its pinned checkout; local work uses the installed module.
module = os.environ.get('ARM_TTK_MODULE', str(Path.home() / 'arm-ttk/arm-ttk/arm-ttk.psd1'))
if not Path(module).is_file():
    sys.exit('FAIL: set ARM_TTK_MODULE to arm-ttk.psd1')
command = r'''
$ErrorActionPreference = 'Continue'
Import-Module $env:ARM_TTK_MODULE -ErrorAction Stop
$toolErrors = @()
$results = @(Test-AzTemplate -TemplatePath $env:FEEDS_TEMPLATE_ROOT -ErrorVariable +toolErrors)
$records = @($results | Where-Object { $_.PSObject.Properties.Name -contains 'Passed' })
$failed = @($records | Where-Object { -not $_.Passed })
# -ErrorVariable also receives TTK's ContinueException control-flow objects.
$actualToolErrors = @($toolErrors | Where-Object { $_ -is [System.Management.Automation.ErrorRecord] })
foreach ($item in $failed) {
    Write-Output $item.Name
    foreach ($testError in $item.Errors) { Write-Output ($testError | Out-String) }
}
Write-Output "ARM-TTK tests=$($records.Count) FAIL=$($failed.Count) toolErrors=$($actualToolErrors.Count) controlFlowRecords=$($toolErrors.Count - $actualToolErrors.Count)"
if ($records.Count -lt 32 -or $failed.Count -gt 0 -or $actualToolErrors.Count -gt 0) { exit 1 }
exit 0
'''
# ARM-TTK inherits the main template's nested-template paths into sibling
# templates. Isolating each preserves full rule coverage without that tool bug.
templates = []
for path in sorted(ROOT.rglob('*.json')):
    if '.git' in path.parts or '.python_packages' in path.parts:
        continue
    try:
        schema = json.loads(path.read_text()).get('$schema', '')
    except (ValueError, UnicodeError, AttributeError):
        continue
    if 'deploymentTemplate.json' in schema:
        templates.append(path)
if len(templates) < 2:
    sys.exit('FAIL: expected both published ARM templates')
failed = False
for template in templates:
    with tempfile.TemporaryDirectory(prefix='feeds-arm-ttk-') as folder:
        (Path(folder)/template.name).write_bytes(template.read_bytes())
        env = dict(os.environ, ARM_TTK_MODULE=module,
                   FEEDS_TEMPLATE_ROOT=str(Path(folder)/template.name))
        result = subprocess.run([pwsh, '-NoProfile', '-Command', command], env=env,
                                capture_output=True, text=True, timeout=300)
        print(template.relative_to(ROOT))
        print(result.stdout)
        if result.returncode:
            print(result.stderr)
            failed = True
sys.exit(1 if failed else 0)
