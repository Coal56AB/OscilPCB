param(
    [string]$InstallDirectory = 'C:\Program Files\SOLIDWORKS Corp\SOLIDWORKS',
    [string]$Templates = 'C:\ProgramData\SOLIDWORKS\SOLIDWORKS 2024\templates',
    [string]$Only = '',
    [string]$Python = 'python',
    [string]$PanelPython = '',
    [switch]$Force
)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Split-Path $PSScriptRoot -Parent)).Path
if ($PanelPython -eq '') { $PanelPython = $Python }
function Invoke-CasePython([string]$executable, [string]$script) {
    & $executable -B (Join-Path $PSScriptRoot $script)
    if ($LASTEXITCODE -ne 0) { throw ('Failed: ' + $script) }
}
if ($Only -eq '' -or -not (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'layout.json'))) {
    Invoke-CasePython $Python 'build_case.py'
    Invoke-CasePython $PanelPython 'build_panel.py'
}
$api = Join-Path $InstallDirectory 'api\redist\SolidWorks.Interop.sldworks.dll'
Add-Type -Path $api
$references = @($api, ([Console].Assembly.Location), ([Security.Cryptography.SHA256].Assembly.Location)) | Select-Object -Unique
Add-Type -ReferencedAssemblies $references -TypeDefinition (Get-Content -Raw -Encoding UTF8 (Join-Path $PSScriptRoot 'SolidWorksCase.cs'))
$camAddin = [SolidWorksCase]::SuspendCam()
try {
    [SolidWorksCase]::Build($root, $Templates, $Only, $Force.IsPresent)
    if ($Only -eq '' -or $Only -in @('front','rear','power_cap','kickstand')) {
        [SolidWorksCase]::ExportPrintMeshes($root, $Only, '', 5)
        $meshArguments = @('-B', (Join-Path $PSScriptRoot 'export_meshes.py'), '--native')
        if ($Only -ne '') { $meshArguments += @('--only', $Only) }
        & $Python @meshArguments
        if ($LASTEXITCODE -ne 0) { throw 'Failed: export_meshes.py' }
    }
    if ($Only -eq '') {
        Invoke-CasePython $Python 'build_glass_dxf.py'
        Invoke-CasePython $Python 'verify_case.py'
        # Only disposable, reproducible build files are removed. Models and source
        # scripts/configuration remain; all paths are checked against this project.
        $generated = @(Get-ChildItem -LiteralPath (Join-Path $root 'output') -File |
            Where-Object { $_.Extension -ne '.stl' -and $_.Name -ne 'panel.png' })
        $generated += @(Get-ChildItem -LiteralPath $PSScriptRoot -File |
            Where-Object { $_.Extension -in @('.plan','.pose','.step','.fingerprint') -or
                $_.Name -in @('layout.json','geometry-check.json') })
        foreach ($file in $generated) {
            $target = [IO.Path]::GetFullPath($file.FullName)
            if (-not $target.StartsWith($root + [IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase)) {
                throw ('Cleanup path is outside enclosure: ' + $target)
            }
            Remove-Item -LiteralPath $target
        }
        foreach ($relative in @('panel','src/__pycache__','output/.native-mesh')) {
            $target = [IO.Path]::GetFullPath((Join-Path $root $relative))
            if (-not $target.StartsWith($root + [IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase)) {
                throw ('Cleanup directory is outside enclosure: ' + $target)
            }
            if (Test-Path -LiteralPath $target) { Remove-Item -LiteralPath $target -Recurse -Force }
        }
    }
} finally {
    [SolidWorksCase]::RestoreCam($camAddin)
}
