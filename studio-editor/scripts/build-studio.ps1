$editorRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$studioRoot = [IO.Path]::GetFullPath((Join-Path $editorRoot '../clip/clip_service/studio_web'))
$previousStudioBuild = $env:STUDIO_BUILD
$previousClipStudio = $env:VITE_CLIP_STUDIO

Push-Location $editorRoot
try {
    $env:STUDIO_BUILD = '1'
    $env:VITE_CLIP_STUDIO = '1'
    npm run build:web
    if ($LASTEXITCODE -ne 0) { throw 'Studio frontend build failed' }

    Copy-Item -LiteralPath (Join-Path $editorRoot 'dist/third-party') -Destination (Join-Path $studioRoot 'third-party') -Recurse -Force
    Copy-Item -LiteralPath (Join-Path $editorRoot 'LICENSE') -Destination (Join-Path $studioRoot 'LICENSE.txt') -Force
}
finally {
    $env:STUDIO_BUILD = $previousStudioBuild
    $env:VITE_CLIP_STUDIO = $previousClipStudio
    Pop-Location
}
