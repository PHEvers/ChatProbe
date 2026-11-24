param(
    [string]$Python = "python",
    [ValidatePattern('^dist(?:/[a-zA-Z0-9_-]+)?\z')]
    [string]$OutputDirectory = "dist"
)

$ErrorActionPreference = "Stop"

function Get-DistDirectory([string]$Path) {
    $directory = Get-Item -LiteralPath $Path -Force
    $prefix = $distPath + [IO.Path]::DirectorySeparatorChar
    if (-not $directory.PSIsContainer -or
        ($directory.FullName -ne $distPath -and
         -not $directory.FullName.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase))) {
        throw "Pasta fora do diretório de distribuição: $Path"
    }
    for ($parent = $directory; $parent.FullName -ne $distPath; $parent = $parent.Parent) {
        if ($parent.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw "A pasta de distribuição contém um vínculo: $($parent.FullName)"
        }
    }
    return $directory
}

function Remove-DistTree([string]$Path) {
    $directory = Get-DistDirectory $Path
    if ($directory.FullName -eq $distPath -or
        (Get-ChildItem -LiteralPath $directory.FullName -Recurse -Force -Attributes ReparsePoint |
            Select-Object -First 1)) {
        throw "Não foi possível confirmar que a pasta pode ser removida: $Path"
    }
    Remove-Item -LiteralPath $directory.FullName -Recurse -Force
}

function Get-PackageExecutable([string]$Package) {
    $executable = Join-Path $Package 'ChatProbe.exe'
    $pythonLibrary = Join-Path $Package '_internal/base_library.zip'
    if ((Test-Path -LiteralPath $executable -PathType Leaf) -and
        (Test-Path -LiteralPath $pythonLibrary -PathType Leaf)) {
        Get-Item -LiteralPath $executable
    }
}

function Remove-OldPackages([string]$CurrentPackage) {
    $previousPackages = foreach ($folder in Get-ChildItem -LiteralPath $distPath -Directory) {
        if ($folder.Name -notmatch '^[a-zA-Z0-9_-]+\z' -or
            ($folder.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            continue
        }
        $package = if ($folder.Name -eq 'ChatProbe') { $folder.FullName } else {
            Join-Path $folder.FullName 'ChatProbe'
        }
        if ($package -ne $CurrentPackage) {
            Get-PackageExecutable $package
        }
    }

    foreach ($executable in $previousPackages | Sort-Object LastWriteTimeUtc -Descending | Select-Object -Skip 2) {
        try {
            $package = $executable.Directory
            Remove-DistTree $package.FullName
            if ($package.Parent.FullName -ne $distPath -and
                -not (Get-ChildItem -LiteralPath $package.Parent.FullName -Force | Select-Object -First 1)) {
                $emptyFolder = Get-DistDirectory $package.Parent.FullName
                [IO.Directory]::Delete($emptyFolder.FullName)
            }
            Write-Host "Pacote antigo removido: $($package.FullName)"
        }
        catch {
            Write-Warning "Não foi possível limpar o pacote antigo $($executable.DirectoryName). $($_.Exception.Message)"
        }
    }
}

Push-Location $PSScriptRoot
$previousBrowsersPath = $env:PLAYWRIGHT_BROWSERS_PATH
$buildLock = $null
$staging = $null
try {
    if ($OutputDirectory -eq 'dist/ChatProbe') {
        throw 'Use dist ou uma subpasta com outro nome. dist/ChatProbe é o pacote padrão.'
    }
    $buildDirectory = New-Item -ItemType Directory -Path build -Force
    $lockPath = Join-Path $buildDirectory.FullName 'windows-build.lock'
    $buildLock = [IO.File]::Open($lockPath, 'OpenOrCreate', 'ReadWrite', 'None')
    $distDirectory = New-Item -ItemType Directory -Path dist -Force
    if ($distDirectory.Attributes -band [IO.FileAttributes]::ReparsePoint) {
        throw 'A pasta dist deve estar no projeto e não pode ser um vínculo.'
    }
    $distPath = $distDirectory.FullName
    $staging = New-Item -ItemType Directory -Path (Join-Path $distPath ".building-$([guid]::NewGuid())")

    $buildPython = "build/windows-venv/Scripts/python.exe"
    if (-not (Test-Path -LiteralPath $buildPython)) {
        & $Python -m venv build/windows-venv
        if ($LASTEXITCODE -ne 0) { throw "Não foi possível criar o ambiente Python de build." }
    }

    & $buildPython -m pip install playwright pyinstaller
    if ($LASTEXITCODE -ne 0) { throw "Não foi possível instalar as dependências do build." }

    $env:PLAYWRIGHT_BROWSERS_PATH = "0"
    & $buildPython -m playwright install --no-shell chromium
    if ($LASTEXITCODE -ne 0) { throw "Não foi possível baixar o Chromium para o pacote." }

    $example = Join-Path $PSScriptRoot "examples/prompts/cupom-nao-impresso.json"
    & $buildPython -m PyInstaller --noconfirm --onedir --windowed --name ChatProbe `
        --paths $PSScriptRoot --specpath build --workpath build/pyinstaller `
        --distpath $staging.FullName `
        --add-data "${example}:examples/prompts" app/desktop.py
    if ($LASTEXITCODE -ne 0) { throw "Não foi possível gerar o ChatProbe.exe." }

    $package = Join-Path $staging.FullName "ChatProbe"
    $executable = Get-PackageExecutable $package
    $bundledExample = Join-Path $package "_internal/examples/prompts/cupom-nao-impresso.json"
    $bundledChromium = Join-Path $package "_internal/playwright/driver/package/.local-browsers"
    if (-not $executable -or
        -not (Test-Path -LiteralPath $bundledExample -PathType Leaf) -or
        -not (Test-Path -LiteralPath $bundledChromium -PathType Container) -or
        -not (Get-ChildItem -LiteralPath $bundledChromium -Filter chrome.exe -Recurse |
              Select-Object -First 1)) {
        throw "O pacote não contém o executável, os arquivos do Python, o roteiro de exemplo ou o Chromium."
    }

    if (-not (Test-Path -LiteralPath $OutputDirectory)) {
        New-Item -ItemType Directory -Path $OutputDirectory | Out-Null
    }
    $destination = Get-DistDirectory $OutputDirectory
    $currentPackage = Join-Path $destination.FullName 'ChatProbe'
    $archive = $null
    if (Test-Path -LiteralPath $currentPackage) {
        $existing = Get-DistDirectory $currentPackage
        if (-not (Get-PackageExecutable $existing.FullName)) {
            throw "O destino já contém uma pasta que não é um pacote do ChatProbe: $currentPackage"
        }
        $archiveName = 'anterior-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff')
        $archive = New-Item -ItemType Directory -Path (Join-Path $distPath $archiveName)
        Move-Item -LiteralPath $existing.FullName -Destination $archive.FullName
    }
    try {
        $readyPackage = Get-DistDirectory $package
        $executable.LastWriteTimeUtc = [DateTime]::UtcNow
        Move-Item -LiteralPath $readyPackage.FullName -Destination $destination.FullName
    }
    catch {
        if ($archive) {
            $backup = Get-DistDirectory (Join-Path $archive.FullName 'ChatProbe')
            Move-Item -LiteralPath $backup.FullName -Destination $destination.FullName
            [IO.Directory]::Delete($archive.FullName)
        }
        throw
    }

    Remove-OldPackages $currentPackage
    Write-Host "Executável pronto: $(Join-Path $currentPackage 'ChatProbe.exe')"
}
finally {
    if ($staging) {
        try {
            Remove-DistTree $staging.FullName
        }
        catch {
            Write-Warning "Não foi possível limpar os arquivos temporários do build. $($_.Exception.Message)"
        }
    }
    if ($buildLock) {
        $buildLock.Dispose()
    }
    $env:PLAYWRIGHT_BROWSERS_PATH = $previousBrowsersPath
    Pop-Location
}
