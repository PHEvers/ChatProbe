param(
    [string]$Python = "python",
    [ValidatePattern('^dist(?:/[a-zA-Z0-9_-]+)?$')]
    [string]$OutputDirectory = "dist"
)

$ErrorActionPreference = "Stop"
Push-Location $PSScriptRoot
$previousBrowsersPath = $env:PLAYWRIGHT_BROWSERS_PATH
try {
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
        --distpath $OutputDirectory `
        --add-data "${example}:examples/prompts" app/desktop.py
    if ($LASTEXITCODE -ne 0) { throw "Não foi possível gerar o ChatProbe.exe." }

    $package = Join-Path $OutputDirectory "ChatProbe"
    $pythonLibrary = Join-Path $package "_internal/base_library.zip"
    $bundledExample = Join-Path $package "_internal/examples/prompts/cupom-nao-impresso.json"
    $bundledChromium = Join-Path $package "_internal/playwright/driver/package/.local-browsers"
    if (-not (Test-Path -LiteralPath "$package/ChatProbe.exe") -or
        -not (Test-Path -LiteralPath $pythonLibrary -PathType Leaf) -or
        -not (Test-Path -LiteralPath $bundledExample -PathType Leaf) -or
        -not (Get-ChildItem -LiteralPath $bundledChromium -Filter chrome.exe -Recurse |
              Select-Object -First 1)) {
        throw "O pacote não contém o executável, os arquivos do Python, o roteiro de exemplo ou o Chromium."
    }
    Write-Host "Executável pronto: $((Resolve-Path -LiteralPath "$package/ChatProbe.exe").Path)"
}
finally {
    $env:PLAYWRIGHT_BROWSERS_PATH = $previousBrowsersPath
    Pop-Location
}
