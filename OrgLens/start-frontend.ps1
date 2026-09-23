$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Join-Path $PSScriptRoot 'frontend')
& npm.cmd run dev -- --host 127.0.0.1

