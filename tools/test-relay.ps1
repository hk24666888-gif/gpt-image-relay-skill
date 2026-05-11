param(
    [string]$Name,
    [string]$Prompt = "A simple studio product photo of one red apple on a white table, realistic lighting, no text, no watermark",
    [string]$Size = "1000x1000",
    [ValidateSet("low", "medium", "high", "auto")]
    [string]$Quality = "low",
    [string]$Out = ".\output\imagegen\relay-test.png",
    [switch]$Live
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($Name)) {
    $Name = Read-Host "Relay short name to test"
}

$python = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if (!(Test-Path -LiteralPath $python)) {
    $python = "python"
}

$script = Join-Path (Split-Path -Parent $PSScriptRoot) "scripts\generate_image.py"
if (!(Test-Path -LiteralPath $script)) {
    $script = Join-Path $env:USERPROFILE ".codex\skills\gpt-image-relay\scripts\generate_image.py"
}
if (!(Test-Path -LiteralPath $script)) {
    $script = Join-Path $env:USERPROFILE ".yukino\skills\gpt-image-relay\scripts\generate_image.py"
}
if (!(Test-Path -LiteralPath $script)) {
    throw "Skill script was not found."
}

$args = @(
    $script,
    "--relay", $Name,
    "--prompt", $Prompt,
    "--size", $Size,
    "--quality", $Quality,
    "--out", $Out,
    "--force"
)

if (!$Live) {
    $args += "--dry-run"
}

& $python @args
