param(
  [string]$Name = "FacebookADB_AutoJob",
  [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$RepositoryRoot = Split-Path -Parent $ProjectRoot
$BuildRoot = Join-Path $env:TEMP "${Name}_pyinstaller_build"
$SpecRoot = $ProjectRoot
$SpecFile = Join-Path $SpecRoot "${Name}.spec"
$DistRoot = Join-Path $ProjectRoot "dist"

New-Item -ItemType Directory -Force -Path $BuildRoot, $DistRoot | Out-Null

Push-Location $ProjectRoot
try {
  if (-not $SkipInstall) {
    python -m pip install -r requirements.txt
    python -m pip install pyinstaller
  }

  python -m compileall -q .
  if ($LASTEXITCODE -ne 0) {
    throw "compileall failed"
  }

  python -m PyInstaller `
    --clean `
    --noconfirm `
    --windowed `
    --name $Name `
    --paths $RepositoryRoot `
    --workpath $BuildRoot `
    --specpath $SpecRoot `
    --distpath $DistRoot `
    --add-data "$ProjectRoot\configs;configs" `
    --add-data "$ProjectRoot\resources;resources" `
    --exclude-module tests `
    --exclude-module tools `
    app.py

  if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller build failed"
  }
  Write-Host "Build output: $ProjectRoot\dist\$Name"
}
finally {
  Pop-Location
  $resolvedProjectRoot = [System.IO.Path]::GetFullPath($ProjectRoot)
  $resolvedSpecFile = [System.IO.Path]::GetFullPath($SpecFile)
  if (-not $resolvedSpecFile.StartsWith($resolvedProjectRoot + [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to remove spec outside project: $resolvedSpecFile"
  }
  if ([System.IO.File]::Exists($resolvedSpecFile)) {
    [System.IO.File]::Delete($resolvedSpecFile)
  }
}
