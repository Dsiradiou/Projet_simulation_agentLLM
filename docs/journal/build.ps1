# Génère journal-de-bord.pdf à la racine du dépôt à partir de docs/journal/journal-de-bord.html.
# Utilise Microsoft Edge en mode headless, présent par défaut sous Windows.
# Usage : powershell -ExecutionPolicy Bypass -File docs/journal/build.ps1

$ErrorActionPreference = "Stop"

$root = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$source = Join-Path $PSScriptRoot "journal-de-bord.html"
$output = Join-Path $root "journal-de-bord.pdf"
$profileDir = Join-Path $env:TEMP "journal-edge-profile"

$edge = @(
    "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
    "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $edge) { throw "Microsoft Edge introuvable." }

# Edge headless reste parfois ouvert apres l'impression, ou bloque avant : on arrete tout Edge
# lance avec le profil du journal, avant et apres la generation, et on borne l'attente.
# (Messages sans accents : Windows PowerShell 5.1 lit les scripts sans BOM en ANSI.)
function Stop-EdgeJournal {
    Get-CimInstance Win32_Process -Filter "Name='msedge.exe'" |
        Where-Object { $_.CommandLine -like "*journal-edge-profile*" } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
}

Stop-EdgeJournal
if (Test-Path $output) { Remove-Item $output -Force }

$uri = ([System.Uri]$source).AbsoluteUri
$arguments = @(
    "--headless", "--disable-gpu", "--no-pdf-header-footer", "--print-to-pdf-no-header",
    "--user-data-dir=`"$profileDir`"", "--print-to-pdf=`"$output`"", $uri
)
Start-Process -FilePath $edge -ArgumentList $arguments -WindowStyle Hidden | Out-Null

# Attend que le PDF existe, puis que sa taille ne change plus (ecriture terminee).
$deadline = (Get-Date).AddSeconds(120)
$taille = -1
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Milliseconds 500
    if (Test-Path $output) {
        $nouvelle = (Get-Item $output).Length
        if ($nouvelle -gt 0 -and $nouvelle -eq $taille) { break }
        $taille = $nouvelle
    }
}
Stop-EdgeJournal
if (-not (Test-Path $output) -or (Get-Item $output).Length -eq 0) {
    throw "Le PDF n'a pas pu etre genere en 2 minutes."
}
Write-Host "PDF genere : $output ($((Get-Item $output).Length) octets)"
