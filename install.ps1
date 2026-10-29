<#
Install the bilingual-reader skill on Windows for Claude Code and Codex.

  .\install.ps1              link the skill into ~/.claude/skills and ~/.codex/skills,
                             and create .venv here with the Python packages (about 100 MB)
  .\install.ps1 -NoVenv      only link the skill
  .\install.ps1 -Agents claude     link it for one agent only

Run it again after moving this folder. The links are directory junctions, so the agents always use
this copy: update it with `git pull`.
#>
param([switch]$NoVenv, [string[]]$Agents = @("claude", "codex"))
$ErrorActionPreference = "Stop"
$repo = $PSScriptRoot

foreach ($agent in $Agents) {
    $dir = Join-Path $HOME ".$agent\skills"
    New-Item -ItemType Directory -Force $dir | Out-Null
    $link = Join-Path $dir "bilingual-reader"
    if (Test-Path $link) {
        $item = Get-Item $link -Force
        if ($item.LinkType) {
            cmd /c rmdir "$link" | Out-Null   # removes the junction, never the files it points to
        } else {
            Write-Warning "$link exists and is not a link; left alone"
            continue
        }
    }
    New-Item -ItemType Junction -Path $link -Target $repo | Out-Null
    Write-Host "linked $link -> $repo"
}

if (-not $NoVenv) {
    $python = Get-Command python -ErrorAction SilentlyContinue
    if (-not $python) { $python = Get-Command py -ErrorAction SilentlyContinue }
    if (-not $python) { throw "Python 3.10 or later is needed: https://www.python.org/downloads/" }
    $venv = Join-Path $repo ".venv"
    if (-not (Test-Path (Join-Path $venv "Scripts\python.exe"))) {
        & $python.Source -m venv $venv
    }
    $vpy = Join-Path $venv "Scripts\python.exe"
    & $vpy -m pip install --upgrade pip | Out-Null
    & $vpy -m pip install -r (Join-Path $repo "requirements.txt")
    & $vpy -I -c "import pymupdf.layout, yaml; print('python packages ok')"
    Write-Host "Python environment: $vpy"
}
Write-Host "Done. In Claude Code or Codex, open a folder with your PDF, EPUB or HTML filing and ask for a bilingual study edition."
