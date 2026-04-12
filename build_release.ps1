# build_release.ps1 — Script de compilacion y publicacion de VRCMT
# Uso: .\build_release.ps1 -Version "2.0.9"
# Si no se pasa -Version, lee la version automaticamente de version_check.py

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
pyinstaller VRCMT.spec --noconfirm
if ($LASTEXITCODE -ne 0) { Write-Error "PyInstaller fallo"; exit 1 }
Write-Host "OK - dist\VRCMT.exe generado"

# ----- 3. Crear copia con version en el nombre -----
$src = "dist\VRCMT.exe"
$dst = "dist\VRCMTv$Version.exe"
Write-Host "`n[2/4] Creando copia versionada: $dst"
Copy-Item $src $dst -Force
Write-Host "OK - $dst creado"

# ----- 4. Commit de version si hay cambios -----
Write-Host "`n[3/4] Verificando cambios en git..."
$status = git status --porcelain
if ($status) {
    git add -A
    git commit -m "release: version $Version"
    git push origin master
    Write-Host "OK - cambios commiteados y pusheados"
} else {
    Write-Host "Sin cambios pendientes en git"
}

# ----- 5. Crear/actualizar GitHub release -----
Write-Host "`n[4/4] Publicando release v$Version en GitHub..."

# Verificar si el release ya existe
$releaseExists = gh release view "v$Version" 2>$null
if ($LASTEXITCODE -eq 0) {
    Write-Host "Release v$Version ya existe — subiendo/actualizando assets..."
    gh release upload "v$Version" $src --clobber
    gh release upload "v$Version" $dst --clobber
} else {
    Write-Host "Creando nuevo release v$Version..."
    gh release create "v$Version" `
        --title "VRCMT v$Version" `
        --notes "VRCMT v$Version - Ver CHANGELOG en el repositorio." `
        $src $dst
}

Write-Host "`n========================================"
Write-Host " Build completado: VRCMT v$Version"
Write-Host " Assets publicados:"
Write-Host "   - VRCMT.exe          (para OTA / actualizacion automatica)"
Write-Host "   - VRCMTv$Version.exe  (para descarga manual)"
Write-Host "========================================"
