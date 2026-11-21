# Gate: prove the package runs on the real Python 3.8 we ship for Windows 7.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File tools\check_py38.ps1
#
# Why a real interpreter and not just ruff --target-version=py38:
# ruff parses newer syntax happily; only an actual 3.8 parser proves that
# nothing 3.9+ slipped in.  The interpreter comes from
# .toolchain/py38 (python-3.8.10-embed-amd64.zip).
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$py = Join-Path $root ".toolchain\py38\python.exe"

if (-not (Test-Path $py)) {
    Write-Host "Python 3.8 not present. Downloading the embeddable build..."
    $zip = Join-Path $root ".toolchain\python-3.8.10-embed-amd64.zip"
    New-Item -ItemType Directory -Force -Path (Join-Path $root ".toolchain") | Out-Null
    & curl.exe -L --fail --max-time 300 -o $zip `
        "https://www.python.org/ftp/python/3.8.10/python-3.8.10-embed-amd64.zip"
    Expand-Archive -Path $zip -DestinationPath (Join-Path $root ".toolchain\py38") -Force
    # NOTE: the ._pth MUST be written without a BOM.  PowerShell 5.1's
    # Set-Content -Encoding UTF8 adds one, which turns the first entry into
    # "\ufeffpython38.zip" and the interpreter dies with
    # "init_fs_encoding: failed to get the Python codec".
    $pth = Join-Path $root ".toolchain\py38\python38._pth"
    $content = "python38.zip`r`n.`r`nLib\site-packages`r`n..\..`r`n"
    [IO.File]::WriteAllText($pth, $content, (New-Object System.Text.UTF8Encoding($false)))
}

Push-Location $root
try {
    $version = & $py -c "import sys; print(sys.version.split()[0])"
    Write-Host "interpreter: Python $version"
    $code = & $py -m unittest discover -s tests 2>&1
    $code | Select-Object -Last 20
    if ($LASTEXITCODE -ne 0) {
        Write-Host "PY38 CHECK FAILED" -ForegroundColor Red
        exit 1
    }
    Write-Host "PY38 CHECK PASSED" -ForegroundColor Green
}
finally {
    Pop-Location
}
