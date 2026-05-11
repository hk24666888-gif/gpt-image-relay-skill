---
name: gpt-image-relay
description: Generate images through configurable OpenAI-compatible image API relays with local API keys, named relay aliases, fuzzy relay matching, custom models, and product/Amazon image sizes. Use when the user asks to create/generate/make an image/photo/product picture with a relay nickname such as 180 or ykn, asks to add/list/remove/switch image relays, or asks for local gpt-image generation without using the built-in image tool.
---

# GPT Image Relay

Use this skill for image generation through the user's configured OpenAI-compatible relay APIs. Do not use the built-in image tool when the user is clearly asking for local relay/API generation.

## Config

Relay config lives outside the skill so GitHub updates do not overwrite local keys or relay choices:

```text
%USERPROFILE%\.gpt-image-relay\relays.json
```

The config stores relay URLs, model names, aliases, and environment variable names. It must not store raw API keys.

Example shape:

```json
{
  "default": "180",
  "defaults": {
    "model": "gpt-image-2",
    "size": "auto",
    "quality": "medium",
    "output_format": "png"
  },
  "relays": {
    "180": {
      "base_url": "https://api.example.com/v1",
      "api_key_env": "RELAY_180_IMAGE_KEY",
      "model": "gpt-image-2",
      "aliases": ["180txt", "txt180"]
    }
  }
}
```

## Add And Manage Relays

Use `tools/relay.ps1`. It prompts interactively when arguments are omitted and hides API key input.

```powershell
& "$env:USERPROFILE\.codex\skills\gpt-image-relay\tools\relay.ps1" add
```

Useful commands:

```powershell
& "$env:USERPROFILE\.codex\skills\gpt-image-relay\tools\relay.ps1" list
& "$env:USERPROFILE\.codex\skills\gpt-image-relay\tools\relay.ps1" default -Name 180
& "$env:USERPROFILE\.codex\skills\gpt-image-relay\tools\relay.ps1" key -Name 180
& "$env:USERPROFILE\.codex\skills\gpt-image-relay\tools\relay.ps1" remove -Name 180
```

If the Codex path is not present, use the same path under `%USERPROFILE%\.yukino\skills\gpt-image-relay`.

Never ask the user to paste API keys into chat. Use `relay.ps1 add` or `relay.ps1 key` so the key is entered locally as hidden input.

## Generate Images

Use `scripts/generate_image.py` with Codex bundled Python when available:

```powershell
$python = "$env:USERPROFILE\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if (!(Test-Path $python)) { $python = "python" }
$script = "$env:USERPROFILE\.codex\skills\gpt-image-relay\scripts\generate_image.py"
if (!(Test-Path $script)) { $script = "$env:USERPROFILE\.yukino\skills\gpt-image-relay\scripts\generate_image.py" }
& $python $script
```

If the user names a relay or any major part of a relay alias, pass it with `--relay`. Matching is fuzzy against relay name, aliases, and host.

Examples:

```text
用 180 生成一张亚马逊主图，1600x1600 -> --relay 180 --size 1600x1600
用 ykn 生成一张产品场景图，1000x1000 -> --relay ykn --size 1000x1000
yukino 生成一张白底水杯图，高质量 -> --relay yukino --quality high
```

If no relay is named, use the configured default relay. If no default exists and multiple relays are configured, list available relays and ask which one to use.

If the user names an unknown relay, run `--list-relays` or report available relays from the error. Do not guess between ambiguous relays.

## Defaults

If the user does not specify size or quality, use config defaults. The initial defaults are:

```text
size: auto
quality: medium
```

Mention the defaults in the final response when useful. Do not block generation just because size or quality was omitted.

For product/Amazon requests with an explicit size like `1600x1600` or `1000x1000`, pass the requested size directly. The script allows arbitrary `WIDTHxHEIGHT` values and will retry with a nearby supported size then resize the saved output if the API rejects a custom size.

Useful size aliases:

```text
amazon, amazon-main, amazon-1600 -> 1600x1600
amazon-1000 -> 1000x1000
1k -> 1024x1024
1k-landscape -> 1536x1024
1k-portrait -> 1024x1536
2k -> 2048x2048
4k -> 3840x2160
```

Useful flags:

```text
--prompt TEXT
--relay NAME_OR_ALIAS
--size auto|amazon-main|1000x1000|1600x1600|WIDTHxHEIGHT
--quality low|medium|high|auto
--model gpt-image-2
--out PATH
--out-dir DIR
--n 1
--dry-run
--force
--list-relays
```

Save generated images under the current workspace, usually:

```text
output/imagegen/<descriptive-name>.png
```

Report whether the call was a dry run or live API call, which relay/model/size/quality was used, and the final saved path.
