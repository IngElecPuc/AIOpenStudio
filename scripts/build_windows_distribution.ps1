param(
    [string]$Python = ".\.venv\Scripts\python.exe",
    [string]$OutputRoot = ".\build\windows-distribution"
)

$ErrorActionPreference = "Stop"
$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$pythonCandidate = if ([System.IO.Path]::IsPathRooted($Python)) {
    $Python
} else {
    Join-Path $repositoryRoot $Python
}
$outputCandidate = if ([System.IO.Path]::IsPathRooted($OutputRoot)) {
    $OutputRoot
} else {
    Join-Path $repositoryRoot $OutputRoot
}
$pythonPath = (Resolve-Path $pythonCandidate).Path
$outputPath = [System.IO.Path]::GetFullPath($outputCandidate)
$distPath = Join-Path $outputPath "dist"
$workPath = Join-Path $outputPath "work"
$releasePath = Join-Path $outputPath "release"
$specPath = Join-Path $repositoryRoot "packaging\windows\aiopenstudio.spec"
$verifyScript = Join-Path $repositoryRoot "scripts\verify_windows_distribution.py"
$packageScript = Join-Path $repositoryRoot "scripts\package_windows_distribution.py"
$inventoryScript = Join-Path $repositoryRoot "scripts\generate_dependency_inventory.py"
$candidateScript = Join-Path $repositoryRoot "scripts\validate_release_candidate.py"
$compliancePath = Join-Path $outputPath "compliance"

& $pythonPath -c "import PyInstaller"
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller no está disponible. Instala el extra distribution solo con autorización."
}

$version = (& $pythonPath -c "import aiopenstudio; print(aiopenstudio.__version__)").Trim()
$sourceEpoch = (& git -C $repositoryRoot log -1 --format=%ct).Trim()
$env:SOURCE_DATE_EPOCH = $sourceEpoch
$env:PYTHONHASHSEED = "0"
$env:AIOPENSTUDIO_COMPLIANCE_DIR = $compliancePath

& $pythonPath $inventoryScript `
    --project aiopenstudio `
    --extra postgres `
    --extra whisper `
    --extra fooocus `
    --include pyinstaller `
    --output-dir $compliancePath `
    --include-runtime-licenses `
    --strict-licenses
if ($LASTEXITCODE -ne 0) { throw "El inventario de licencias está incompleto." }

& $pythonPath -m PyInstaller `
    --noconfirm `
    --clean `
    --distpath $distPath `
    --workpath $workPath `
    $specPath
if ($LASTEXITCODE -ne 0) { throw "PyInstaller falló." }

$bundlePath = Join-Path $distPath "AIOpenStudio"
$verifyArguments = @(
    $verifyScript,
    $bundlePath,
    "--forbid", $env:USERPROFILE,
    "--forbid", $env:USERNAME,
    "--forbid", $repositoryRoot
)
& $pythonPath @verifyArguments
if ($LASTEXITCODE -ne 0) { throw "El bundle contiene datos o rutas privadas." }

& $pythonPath $packageScript `
    $bundlePath `
    --output-dir $releasePath `
    --version $version `
    --source-date-epoch $sourceEpoch
if ($LASTEXITCODE -ne 0) { throw "No se pudo crear el artefacto reproducible." }

$archivePath = Join-Path $releasePath "AIOpenStudio-$version-windows-x86_64.zip"
$manifestPath = Join-Path $releasePath "AIOpenStudio-$version-windows-x86_64.json"
$reportPath = Join-Path $releasePath "AIOpenStudio-$version-candidate-report.json"
& $pythonPath $candidateScript `
    --bundle $bundlePath `
    --archive $archivePath `
    --manifest $manifestPath `
    --expected-version $version `
    --report $reportPath `
    --forbid $env:USERPROFILE `
    --forbid $env:USERNAME `
    --forbid $repositoryRoot
if ($LASTEXITCODE -ne 0) { throw "El artefacto no supera la barrera de candidatura." }
