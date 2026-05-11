param(
    [ValidateSet("add", "list", "default", "remove", "key", "path")]
    [string]$Action,
    [string]$Name,
    [string]$BaseUrl,
    [string]$Model,
    [string]$ApiKeyEnv,
    [string]$ApiKey,
    [string[]]$Aliases,
    [switch]$MakeDefault,
    [switch]$SkipKeySave,
    [string]$ConfigPath = "$env:USERPROFILE\.gpt-image-relay\relays.json"
)

$ErrorActionPreference = "Stop"

function Convert-SecureStringToPlainText([securestring]$Value) {
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Value)
    try {
        return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    }
}

function Normalize-BaseUrl([string]$Url) {
    $clean = $Url.Trim().TrimEnd("/")
    if ($clean -notmatch "/v\d+$") {
        $clean = "$clean/v1"
    }
    return $clean
}

function Get-DefaultApiKeyEnv([string]$RelayName) {
    $safe = $RelayName.ToUpperInvariant() -replace "[^A-Z0-9]", "_"
    if ($safe -match "^[0-9]") {
        $safe = "RELAY_$safe"
    }
    return "${safe}_IMAGE_KEY"
}

function Set-JsonProperty($Object, [string]$Name, $Value) {
    if ($Object.PSObject.Properties.Name -contains $Name) {
        $Object.$Name = $Value
    } else {
        $Object | Add-Member -MemberType NoteProperty -Name $Name -Value $Value
    }
}

function Remove-JsonProperty($Object, [string]$Name) {
    if ($Object.PSObject.Properties.Name -contains $Name) {
        $Object.PSObject.Properties.Remove($Name)
    }
}

function Read-RelayConfig([string]$Path) {
    if (Test-Path -LiteralPath $Path) {
        return Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json
    }
    [pscustomobject]@{
        default = ""
        defaults = [pscustomobject]@{
            model = "gpt-image-2"
            size = "auto"
            quality = "medium"
            output_format = "png"
        }
        relays = [pscustomobject]@{}
    }
}

function Save-RelayConfig($Config, [string]$Path) {
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Path) | Out-Null
    $Config | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $Path -Encoding UTF8
}

function Ensure-RelayShape($Config) {
    if ($Config.PSObject.Properties.Name -notcontains "defaults" -or $null -eq $Config.defaults) {
        Set-JsonProperty $Config "defaults" ([pscustomobject]@{})
    }
    if ($Config.PSObject.Properties.Name -notcontains "relays" -or $null -eq $Config.relays) {
        Set-JsonProperty $Config "relays" ([pscustomobject]@{})
    }
    if ($Config.PSObject.Properties.Name -notcontains "default") {
        Set-JsonProperty $Config "default" ""
    }
}

if ([string]::IsNullOrWhiteSpace($Action)) {
    $Action = Read-Host "Action: add, list, default, remove, key, path"
}

$config = Read-RelayConfig $ConfigPath
Ensure-RelayShape $config

switch ($Action) {
    "path" {
        Write-Host $ConfigPath
    }

    "list" {
        Write-Host "Config: $ConfigPath"
        Write-Host "Default: $($config.default)"
        Write-Host ""
        foreach ($prop in $config.relays.PSObject.Properties | Sort-Object Name) {
            $relay = $prop.Value
            $aliasText = ""
            if ($relay.aliases) {
                $aliasText = " aliases=$($relay.aliases -join ',')"
            }
            Write-Host "$($prop.Name): $($relay.base_url) model=$($relay.model) key_env=$($relay.api_key_env)$aliasText"
        }
    }

    "add" {
        if ([string]::IsNullOrWhiteSpace($Name)) {
            $Name = Read-Host "Relay short name, for example 180 or ykn"
        }
        if ([string]::IsNullOrWhiteSpace($BaseUrl)) {
            $BaseUrl = Read-Host "Relay base URL, for example https://api.example.com"
        }
        if ([string]::IsNullOrWhiteSpace($Model)) {
            $Model = Read-Host "Model [gpt-image-2]"
            if ([string]::IsNullOrWhiteSpace($Model)) {
                $Model = "gpt-image-2"
            }
        }
        if ($null -eq $Aliases -or $Aliases.Count -eq 0) {
            $aliasInput = Read-Host "Aliases, comma-separated, optional"
            if (![string]::IsNullOrWhiteSpace($aliasInput)) {
                $Aliases = $aliasInput.Split(",") | ForEach-Object { $_.Trim() } | Where-Object { $_ }
            } else {
                $Aliases = @()
            }
        }
        if ([string]::IsNullOrWhiteSpace($ApiKeyEnv)) {
            $suggested = Get-DefaultApiKeyEnv $Name
            $ApiKeyEnv = Read-Host "API key environment variable name [$suggested]"
            if ([string]::IsNullOrWhiteSpace($ApiKeyEnv)) {
                $ApiKeyEnv = $suggested
            }
        }
        if ([string]::IsNullOrWhiteSpace($ApiKey) -and !$SkipKeySave) {
            $secure = Read-Host "API key for $Name (hidden; leave blank to skip saving key)" -AsSecureString
            $ApiKey = Convert-SecureStringToPlainText $secure
        }

        if (![string]::IsNullOrWhiteSpace($ApiKey) -and !$SkipKeySave) {
            [Environment]::SetEnvironmentVariable($ApiKeyEnv, $ApiKey, "User")
            Set-Item -Path "Env:$ApiKeyEnv" -Value $ApiKey
        }

        $relay = [pscustomobject]@{
            base_url = Normalize-BaseUrl $BaseUrl
            api_key_env = $ApiKeyEnv
            model = $Model
            aliases = @($Aliases)
        }
        Set-JsonProperty $config.relays $Name $relay

        if ($MakeDefault -or [string]::IsNullOrWhiteSpace([string]$config.default)) {
            Set-JsonProperty $config "default" $Name
        }

        Save-RelayConfig $config $ConfigPath
        Write-Host "Saved relay '$Name' -> $($relay.base_url)"
        Write-Host "Config: $ConfigPath"
    }

    "default" {
        if ([string]::IsNullOrWhiteSpace($Name)) {
            $Name = Read-Host "Relay name to set as default"
        }
        if ($config.relays.PSObject.Properties.Name -notcontains $Name) {
            throw "Relay '$Name' was not found. Run list to see configured relays."
        }
        Set-JsonProperty $config "default" $Name
        Save-RelayConfig $config $ConfigPath
        Write-Host "Default relay is now '$Name'."
    }

    "remove" {
        if ([string]::IsNullOrWhiteSpace($Name)) {
            $Name = Read-Host "Relay name to remove"
        }
        Remove-JsonProperty $config.relays $Name
        if ($config.default -eq $Name) {
            Set-JsonProperty $config "default" ""
        }
        Save-RelayConfig $config $ConfigPath
        Write-Host "Removed relay '$Name'."
    }

    "key" {
        if ([string]::IsNullOrWhiteSpace($Name)) {
            $Name = Read-Host "Relay name"
        }
        if ($config.relays.PSObject.Properties.Name -notcontains $Name) {
            throw "Relay '$Name' was not found. Run list to see configured relays."
        }
        $relay = $config.relays.$Name
        $envName = $relay.api_key_env
        $secure = Read-Host "New API key for $Name (hidden)" -AsSecureString
        $plain = Convert-SecureStringToPlainText $secure
        if ([string]::IsNullOrWhiteSpace($plain)) {
            throw "No API key entered."
        }
        [Environment]::SetEnvironmentVariable($envName, $plain, "User")
        Set-Item -Path "Env:$envName" -Value $plain
        Write-Host "Updated key environment variable: $envName"
    }
}
