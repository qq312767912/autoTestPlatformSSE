[CmdletBinding()]
param(
    [string]$OutputRoot = "$env:USERPROFILE\Desktop"
)

$ErrorActionPreference = "Continue"
$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$reportDir = Join-Path $OutputRoot "codex-extension-diagnostics-$timestamp"
$reportFile = Join-Path $reportDir "report.txt"
$logsDir = Join-Path $reportDir "logs"

New-Item -ItemType Directory -Force -Path $reportDir, $logsDir | Out-Null

function Add-Section {
    param([string]$Title)
    Add-Content -Path $reportFile -Encoding UTF8 -Value "`r`n==================== $Title ====================`r`n"
}

function Add-Report {
    param([AllowEmptyString()][string]$Text)
    Add-Content -Path $reportFile -Encoding UTF8 -Value $Text
}

function Protect-Secrets {
    param([AllowEmptyString()][string]$Text)
    if ($null -eq $Text) { return "" }

    $result = $Text
    $result = $result -replace '(?i)(api[_-]?key|access[_-]?token|refresh[_-]?token|authorization|password|secret|cookie)(\s*[=:]\s*)[^\s,;"'']+', '$1$2<REDACTED>'
    $result = $result -replace '(?i)Bearer\s+[A-Za-z0-9._~+/-]+=*', 'Bearer <REDACTED>'
    $result = $result -replace 'sk-[A-Za-z0-9_-]{12,}', '<REDACTED_OPENAI_KEY>'
    $result = $result -replace 'gh[pousr]_[A-Za-z0-9_]{12,}', '<REDACTED_GITHUB_TOKEN>'
    $result = $result -replace 'github_pat_[A-Za-z0-9_]{12,}', '<REDACTED_GITHUB_TOKEN>'
    return $result
}

function Invoke-Captured {
    param(
        [string]$Label,
        [scriptblock]$Command
    )
    Add-Report "--- $Label ---"
    try {
        $output = & $Command 2>&1 | Out-String
        Add-Report (Protect-Secrets $output.TrimEnd())
    } catch {
        Add-Report "ERROR: $($_.Exception.Message)"
    }
    Add-Report ""
}

function Copy-RedactedLog {
    param([System.IO.FileInfo]$File)
    try {
        $relativeName = ($File.FullName -replace '[:\\/]', '_')
        $destination = Join-Path $logsDir $relativeName
        $content = Get-Content -LiteralPath $File.FullName -Tail 1200 -ErrorAction Stop | Out-String
        [System.IO.File]::WriteAllText($destination, (Protect-Secrets $content), [System.Text.Encoding]::UTF8)
        Add-Report "Collected: $($File.FullName)"
    } catch {
        Add-Report "Skipped: $($File.FullName) ($($_.Exception.Message))"
    }
}

Add-Report "Codex Extension Diagnostics"
Add-Report "Generated: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss K')"
Add-Report "This report intentionally redacts common secrets and does not collect project source files."

Add-Section "Windows"
Invoke-Captured "Windows version" { Get-CimInstance Win32_OperatingSystem | Select-Object Caption, Version, BuildNumber, OSArchitecture | Format-List }
Invoke-Captured "PowerShell" { $PSVersionTable | Format-List }
Invoke-Captured "Locale" { Get-Culture; Get-WinSystemLocale; Get-TimeZone }

Add-Section "VS Code and Codex extension"
Invoke-Captured "code --version" { code --version }
Invoke-Captured "Installed extensions" { code --list-extensions --show-versions | Sort-Object }
Invoke-Captured "VS Code processes" { Get-Process code -ErrorAction SilentlyContinue | Select-Object Id, ProcessName, Path, StartTime | Format-Table -AutoSize }

$extensionRoots = @(
    "$env:USERPROFILE\.vscode\extensions",
    "$env:USERPROFILE\.vscode-insiders\extensions",
    "$env:USERPROFILE\.cursor\extensions",
    "$env:USERPROFILE\.windsurf\extensions"
) | Where-Object { Test-Path $_ }

foreach ($root in $extensionRoots) {
    Invoke-Captured "Codex/OpenAI extension directories under $root" {
        Get-ChildItem -LiteralPath $root -Directory -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -match '(?i)openai|codex|chatgpt' } |
            Select-Object Name, FullName, LastWriteTime | Format-Table -AutoSize
    }
}

Add-Section "WSL"
Invoke-Captured "wsl --status" { wsl.exe --status }
Invoke-Captured "wsl --version" { wsl.exe --version }
Invoke-Captured "wsl distributions" { wsl.exe --list --verbose }

Add-Section "Codex configuration summary"
$codexRoot = Join-Path $env:USERPROFILE ".codex"
Add-Report "Codex root exists: $(Test-Path $codexRoot)"

$configPath = Join-Path $codexRoot "config.toml"
if (Test-Path $configPath) {
    Add-Report "config.toml exists: True"
    try {
        $configLines = Get-Content -LiteralPath $configPath -ErrorAction Stop
        foreach ($line in $configLines) {
            if ($line -match '^\s*([A-Za-z0-9_.-]+)\s*=') {
                Add-Report ("config key: " + $Matches[1])
            } elseif ($line -match '^\s*\[.+\]\s*$') {
                Add-Report (Protect-Secrets $line)
            }
        }
    } catch {
        Add-Report "Could not inspect config.toml: $($_.Exception.Message)"
    }
} else {
    Add-Report "config.toml exists: False"
}

$codexEnvPath = Join-Path $codexRoot ".env"
if (Test-Path $codexEnvPath) {
    Add-Report ".codex/.env exists: True (values not collected)"
    try {
        Get-Content -LiteralPath $codexEnvPath |
            Where-Object { $_ -match '^\s*[A-Za-z_][A-Za-z0-9_]*\s*=' } |
            ForEach-Object { Add-Report ("env key: " + (($_ -split '=', 2)[0].Trim())) }
    } catch {
        Add-Report "Could not inspect .codex/.env names: $($_.Exception.Message)"
    }
} else {
    Add-Report ".codex/.env exists: False"
}

Add-Section "Relevant environment variable names"
$relevantEnvNames = Get-ChildItem Env: |
    Where-Object { $_.Name -match '(?i)OPENAI|CODEX|HTTP_PROXY|HTTPS_PROXY|ALL_PROXY|NO_PROXY|SSL_CERT|NODE_EXTRA_CA_CERTS' } |
    Select-Object -ExpandProperty Name |
    Sort-Object
if ($relevantEnvNames) {
    $relevantEnvNames | ForEach-Object { Add-Report "present: $_" }
} else {
    Add-Report "No relevant environment variables detected."
}

Add-Section "Network checks"
foreach ($hostName in @("chatgpt.com", "api.openai.com", "developers.openai.com")) {
    Invoke-Captured "DNS $hostName" { Resolve-DnsName $hostName -ErrorAction Stop | Select-Object Name, Type, IPAddress | Format-Table -AutoSize }
    Invoke-Captured "TCP 443 $hostName" { Test-NetConnection $hostName -Port 443 -InformationLevel Detailed }
}

Add-Section "VS Code and Codex logs"
$logRoots = @(
    "$env:APPDATA\Code\logs",
    "$env:APPDATA\Code - Insiders\logs",
    "$env:APPDATA\Cursor\logs",
    "$env:APPDATA\Windsurf\logs",
    "$codexRoot\log",
    "$codexRoot\logs"
) | Where-Object { Test-Path $_ }

$candidateLogs = foreach ($root in $logRoots) {
    Get-ChildItem -LiteralPath $root -Recurse -File -ErrorAction SilentlyContinue |
        Where-Object {
            $_.Name -match '(?i)\.log$|\.txt$' -and
            ($_.FullName -match '(?i)codex|openai|chatgpt|exthost|extensionhost|renderer|sharedprocess|window')
        }
}

$candidateLogs = $candidateLogs |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 40

if ($candidateLogs) {
    foreach ($file in $candidateLogs) { Copy-RedactedLog $file }
} else {
    Add-Report "No matching logs were found automatically."
}

Add-Section "Recent Windows application errors"
Invoke-Captured "Application errors from last 24 hours" {
    $startTime = (Get-Date).AddHours(-24)
    Get-WinEvent -FilterHashtable @{ LogName = 'Application'; StartTime = $startTime; Level = 2 } -ErrorAction Stop |
        Where-Object { $_.ProviderName -match '(?i)Code|Electron|Node|Application Error' -or $_.Message -match '(?i)Code.exe|Codex|OpenAI' } |
        Select-Object -First 30 TimeCreated, ProviderName, Id, Message |
        Format-List
}

$zipPath = "$reportDir.zip"
try {
    Compress-Archive -Path $reportDir -DestinationPath $zipPath -Force
    Write-Host ""
    Write-Host "Diagnostics complete." -ForegroundColor Green
    Write-Host "ZIP: $zipPath" -ForegroundColor Cyan
    Write-Host "Please send this ZIP back for analysis." -ForegroundColor Yellow
} catch {
    Write-Warning "Could not create ZIP: $($_.Exception.Message)"
    Write-Host "Report directory: $reportDir"
}
