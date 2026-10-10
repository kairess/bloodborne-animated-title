# Installs a build from build\<Variant> as mods\bloodborne-animated-title (replacing any previous copy), or removes it.
#   powershell -ExecutionPolicy Bypass -File install.ps1 animated_title
#   powershell -ExecutionPolicy Bypass -File install.ps1 -Remove
#   powershell -ExecutionPolicy Bypass -File install.ps1 animated_title -Mods D:\bbport\mods
param([string]$Variant = 'animated_title', [string]$Mods = 'C:\bbport-windows\mods', [switch]$Remove)

$dest = Join-Path $Mods 'bloodborne-animated-title'
if (Test-Path $dest) { Remove-Item -Recurse -Force $dest }
if ($Remove) { "removed $dest"; return }

$src = Join-Path $PSScriptRoot "build\$Variant"
if (-not (Test-Path $src)) { throw "no build for '$Variant' (run make_reverse.py first)" }
New-Item -ItemType Directory -Force $dest | Out-Null
Copy-Item -Recurse (Join-Path $src 'dvdroot_ps4') (Join-Path $dest 'dvdroot_ps4') -Force
"installed $Variant -> $dest"
Get-ChildItem -Recurse -File $dest | Select-Object Length, Name
