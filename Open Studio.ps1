$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$clipRoot = Join-Path $root 'clip'
$python = Join-Path $clipRoot '.venv\Scripts\python.exe'
$ffmpeg = Join-Path $root 'clip\tools\ffmpeg\ffmpeg-9.0.1-essentials_build\bin'
if (-not (Test-Path -LiteralPath $python)) { throw "Studio environment is missing: $python" }
if (Test-Path -LiteralPath (Join-Path $ffmpeg 'ffmpeg.exe')) {
  $env:PATH = "$ffmpeg;$env:PATH"
}
elseif (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue) -or -not (Get-Command ffprobe -ErrorAction SilentlyContinue)) {
  throw 'FFmpeg and FFprobe are required. Install them and add their bin directory to PATH.'
}
$env:DATABASE_URL = 'sqlite:///./gaming-run.db'
$env:CLIP_STORAGE_ROOT = './gaming-run-storage'
$env:PYTHONPATH = '.'
$port = 8081
try { Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$port/health" -TimeoutSec 1 | Out-Null } catch {
  while ($true) {
    $listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, $port)
    try { $listener.Start(); $listener.Stop(); break } catch { $listener.Stop(); $port++ }
  }
  $args = @('-m','uvicorn','clip_service.api:app','--host','127.0.0.1','--port',$port)
  Start-Process -FilePath $python -ArgumentList $args -WorkingDirectory $clipRoot -WindowStyle Hidden | Out-Null
  $ready = $false
  for ($i = 0; $i -lt 40; $i++) {
    Start-Sleep -Milliseconds 250
    try { Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$port/health" -TimeoutSec 1 | Out-Null; $ready = $true; break } catch {}
  }
  if (-not $ready) { throw "Studio server failed to start on port $port" }
}
Start-Process "http://127.0.0.1:$port/studio/"
