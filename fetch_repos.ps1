$ErrorActionPreference = "Stop"

$Repos = @(
    @{ Name = "ShortGPT"; Url = "https://github.com/RayVentura/ShortGPT.git" },
    @{ Name = "SaarD00"; Url = "https://github.com/SaarD00/AI-Youtube-Shorts-Generator.git" },
    @{ Name = "ViMax"; Url = "https://github.com/HKUDS/ViMax.git" },
    @{ Name = "auto_CM_director"; Url = "https://github.com/naki0227/auto_CM_director.git" }
)

$TargetDir = "workspace\temp\repos"
if (-not (Test-Path $TargetDir)) {
    New-Item -ItemType Directory -Path $TargetDir -Force | Out-Null
}

foreach ($Repo in $Repos) {
    $RepoPath = Join-Path $TargetDir $Repo.Name
    if (-not (Test-Path $RepoPath)) {
        Write-Host "Clonando $($Repo.Name)..."
        git clone --depth 1 $Repo.Url $RepoPath
    }
    else {
        Write-Host "$($Repo.Name) ya existe, omitiendo clonación."
    }
}

Write-Host "¡Descarga de repositorios finalizada!"
