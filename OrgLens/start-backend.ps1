$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not (Test-Path -LiteralPath '.venv/Scripts/python.exe')) {
    throw 'Сначала создайте .venv и установите backend/requirements.txt по инструкции README.'
}
& '.\.venv\Scripts\python.exe' -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000

