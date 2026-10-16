# Génère journal-de-bord.pdf à la racine du dépôt à partir de docs/journal/journal-de-bord.html.
# Utilise Microsoft Edge en mode headless, présent par défaut sous Windows.
# Usage : powershell -ExecutionPolicy Bypass -File docs/journal/build.ps1

$ErrorActionPreference = "Stop"

$root = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$source = Join-Path $PSScriptRoot "journal-de-bord.html"
$output = Join-Path $root "journal-de-bord.pdf"

$edge = @(
    "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
    "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1

if (-not $edge) { throw "Microsoft Edge introuvable." }

# Messages sans accents : Windows PowerShell 5.1 lit les scripts sans BOM en ANSI.
if (Test-Path $output) { Remove-Item $output -Force }

$uri = ([System.Uri]$source).AbsoluteUri
$profileDir = Join-Path $env:TEMP "journal-edge-profile"

& $edge --headless --disable-gpu --no-pdf-header-footer --print-to-pdf-no-header `
    --user-data-dir="$profileDir" --print-to-pdf="$output" $uri | Out-Null

# Edge rend la main avant d'avoir fini d'écrire le fichier : on attend qu'il apparaisse.
$deadline = (Get-Date).AddSeconds(30)
while (-not (Test-Path $output) -and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 300 }
if (-not (Test-Path $output)) { throw "Le PDF n'a pas pu etre genere." }

Write-Host "PDF genere : $output"
