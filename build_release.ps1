# build_release.ps1 — Compilacion y publicacion de VRCMT
# Uso: .\build_release.ps1 [-Version "2.0.12"]
# IMPORTANTE: Este script NUNCA sube codigo fuente a GitHub.
#             Solo actualiza version.txt via API y sube el .exe como release asset.

param(
    [string]$Version = ""
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

# ----- 1. Leer version si no se paso como parametro -----
if (-not $Version) {
    $line = Select-String -Path "src\core\version_check.py" -Pattern 'CURRENT_VERSION\s*=\s*"([^"]+)"'
    if ($line) {
        $Version = $line.Matches[0].Groups[1].Value
    } else {
        Write-Error "No se pudo leer CURRENT_VERSION de version_check.py"
        exit 1
    }
}
Write-Host "========================================"
Write-Host " VRCMT Build v$Version"
Write-Host "========================================"

# ----- 2. Compilar con PyInstaller -----
Write-Host "`n[1/4] Compilando VRCMT.exe con PyInstaller..."
Stop-Process -Name "VRCMT" -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 1
pyinstaller VRCMT.spec --noconfirm
if ($LASTEXITCODE -ne 0) { Write-Error "PyInstaller fallo"; exit 1 }
Write-Host "OK - dist\VRCMT.exe generado"

# ----- 3. Crear copia con version en el nombre -----
$src = "dist\VRCMT.exe"
$dst = "dist\VRCMTv$Version.exe"
Write-Host "`n[2/4] Creando copia versionada: $dst"
Copy-Item $src $dst -Force
Write-Host "OK - $dst creado"

# ----- 4. Actualizar version.txt en GitHub via API (sin subir codigo fuente) -----
Write-Host "`n[3/4] Actualizando version.txt en GitHub (solo version, sin codigo)..."

# Obtener SHA actual del archivo version.txt en el repo publico
$repoFile = gh api repos/doctorlatex/VRCMT/contents/version.txt 2>$null | ConvertFrom-Json
if ($repoFile -and $repoFile.sha) {
    $sha = $repoFile.sha
    $contentB64 = [Convert]::ToBase64String([System.Text.Encoding]::ASCII.GetBytes($Version))
    gh api repos/doctorlatex/VRCMT/contents/version.txt `
        --method PUT `
        -f message="release: v$Version" `
        -f content=$contentB64 `
        -f sha=$sha | Out-Null
    Write-Host "OK - version.txt actualizado a $Version en GitHub"
} else {
    Write-Warning "No se pudo obtener SHA de version.txt - saltando actualizacion de version.txt"
}

# ----- 5. Crear/actualizar GitHub release -----
Write-Host "`n[4/4] Publicando release v$Version en GitHub..."

$releaseExists = gh release view "v$Version" 2>$null
if ($LASTEXITCODE -eq 0) {
    Write-Host "Release v$Version ya existe - actualizando assets..."
    gh release upload "v$Version" $src --clobber
    gh release upload "v$Version" $dst --clobber
} else {
    Write-Host "Creando nuevo release v$Version..."
    gh release create "v$Version" `
        --title "VRCMT v$Version" `
        --notes "VRCMT v$Version" `
        $src $dst
}

Write-Host "`n========================================"
Write-Host " Build completado: VRCMT v$Version"
Write-Host " GitHub: solo version.txt actualizado (sin codigo fuente)"
Write-Host " Assets publicados en release:"
Write-Host "   - VRCMT.exe           (para OTA / actualizacion automatica)"
Write-Host "   - VRCMTv$Version.exe  (para descarga manual)"
Write-Host "========================================"
