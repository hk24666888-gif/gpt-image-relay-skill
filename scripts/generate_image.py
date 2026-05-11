#!/usr/bin/env python3
"""Generate images through configurable OpenAI-compatible image API relays."""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


DEFAULT_API_KEY_ENV = "OPENAI_API_KEY"
DEFAULT_MODEL = "gpt-image-2"
DEFAULT_SIZE = "auto"
DEFAULT_QUALITY = "medium"
DEFAULT_OUTPUT_FORMAT = "png"
DEFAULT_TIMEOUT = 600.0

SIZE_ALIASES = {
    "auto": "auto",
    "amazon": "1600x1600",
    "amazon-main": "1600x1600",
    "amazon-1600": "1600x1600",
    "amazon-1000": "1000x1000",
    "1k": "1024x1024",
    "1k-square": "1024x1024",
    "1k-landscape": "1536x1024",
    "1k-portrait": "1024x1536",
    "2k": "2048x2048",
    "2k-square": "2048x2048",
    "2k-landscape": "2048x1152",
    "2k-portrait": "1152x2048",
    "4k": "3840x2160",
    "4k-square": "3840x3840",
    "4k-landscape": "3840x2160",
    "4k-portrait": "2160x3840",
}

FALLBACK_SIZES = {
    "square": "1024x1024",
    "landscape": "1536x1024",
    "portrait": "1024x1536",
}


class APIRequestError(RuntimeError):
    def __init__(self, message: str, status: int | None = None, detail: str | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.detail = detail


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate images through named API relay aliases.")
    parser.add_argument("--prompt", help="Image generation prompt.")
    parser.add_argument("--relay", help="Relay name, alias, or partial name from relays.json.")
    parser.add_argument("--config", help="Path to relay config JSON.")
    parser.add_argument("--list-relays", action="store_true", help="List configured relays and exit.")
    parser.add_argument("--out", help="Output file path. For n>1, suffixes are added.")
    parser.add_argument("--out-dir", help="Output directory. Files are named image_1.ext.")
    parser.add_argument("--model", help="Image model, for example gpt-image-2 or gpt-image-3.")
    parser.add_argument("--size", help="Image size or alias. Arbitrary WxH values are allowed.")
    parser.add_argument("--quality", choices=["low", "medium", "high", "auto"], help="Image quality.")
    parser.add_argument("--output-format", choices=["png", "jpeg", "webp"], help="Output image format.")
    parser.add_argument("--n", type=int, default=1, help="Number of images to generate.")
    parser.add_argument("--timeout", type=float, help="HTTP timeout in seconds.")
    parser.add_argument("--base-url", help="OpenAI-compatible base URL, usually ending in /v1.")
    parser.add_argument("--api-key-env", help="Environment variable containing this relay's API key.")
    parser.add_argument("--dry-run", action="store_true", help="Print request details without network or API key.")
    parser.add_argument("--force", action="store_true", help="Overwrite existing output files.")
    parser.add_argument("--no-fallback-resize", action="store_true", help="Do not retry unsupported custom sizes.")
    parser.add_argument("--no-ensure-output-size", action="store_true", help="Do not resize saved files to requested WxH.")
    return parser.parse_args()


def user_config_path() -> Path:
    return Path.home() / ".gpt-image-relay" / "relays.json"


def config_paths(args: argparse.Namespace) -> list[Path]:
    if args.config:
        return [Path(args.config).expanduser()]

    env_path = os.environ.get("GPT_IMAGE_RELAY_CONFIG")
    if env_path:
        return [Path(env_path).expanduser()]

    return [user_config_path()]


def load_config(args: argparse.Namespace) -> tuple[dict[str, Any], Path | None]:
    for path in config_paths(args):
        if path.exists():
            with path.open("r", encoding="utf-8-sig") as handle:
                data = json.load(handle)
            if not isinstance(data, dict):
                raise SystemExit(f"Relay config must be a JSON object: {path}")
            return data, path
    return {}, None


def normalize_base_url(url: str) -> str:
    clean = url.strip().rstrip("/")
    if not re.search(r"/v\d+$", clean):
        clean = f"{clean}/v1"
    return clean


def normalize_match_text(value: Any) -> str:
    text = str(value).strip().lower()
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", text)


def relay_entries(config: dict[str, Any]) -> dict[str, Any]:
    relays = config.get("relays", {})
    if not isinstance(relays, dict):
        raise SystemExit("Relay config must contain an object named relays.")
    return relays


def relay_labels(name: str, relay: dict[str, Any]) -> list[str]:
    labels = [name]
    aliases = relay.get("aliases", [])
    if isinstance(aliases, str):
        labels.extend(part.strip() for part in aliases.split(",") if part.strip())
    elif isinstance(aliases, list):
        labels.extend(str(item) for item in aliases)

    base_url = relay.get("base_url")
    if base_url:
        labels.append(str(base_url))
        host = urllib.parse.urlparse(str(base_url)).netloc
        if host:
            labels.append(host)
            labels.append(host.replace("api.", "", 1))

    return labels


def match_score(query: str, candidate: str) -> int:
    if not query or not candidate:
        return 0
    if query == candidate:
        return 100
    if candidate.startswith(query):
        return 85
    if query.startswith(candidate) and len(candidate) >= 3:
        return 75
    if len(query) >= 2 and query in candidate:
        return 65
    if len(candidate) >= 3 and candidate in query:
        return 55
    return 0


def find_relay(relays: dict[str, Any], query: str) -> tuple[str, dict[str, Any]]:
    normalized_query = normalize_match_text(query)
    matches: list[tuple[int, str, dict[str, Any]]] = []

    for name, value in relays.items():
        if not isinstance(value, dict):
            continue
        best = 0
        for label in relay_labels(str(name), value):
            best = max(best, match_score(normalized_query, normalize_match_text(label)))
        if best:
            matches.append((best, str(name), value))

    if not matches:
        raise SystemExit(f"Relay '{query}' was not found. Available relays: {format_relay_names(relays)}")

    matches.sort(key=lambda item: (-item[0], item[1].lower()))
    top_score = matches[0][0]
    top = [item for item in matches if item[0] == top_score]
    if len(top) > 1:
        names = ", ".join(item[1] for item in top)
        raise SystemExit(f"Relay '{query}' is ambiguous. Matching relays: {names}")

    return matches[0][1], matches[0][2]


def format_relay_names(relays: dict[str, Any]) -> str:
    names = []
    for name, value in relays.items():
        if not isinstance(value, dict):
            continue
        aliases = value.get("aliases", [])
        if isinstance(aliases, list) and aliases:
            names.append(f"{name} ({', '.join(str(item) for item in aliases)})")
        elif isinstance(aliases, str) and aliases.strip():
            names.append(f"{name} ({aliases})")
        else:
            names.append(str(name))
    return ", ".join(sorted(names, key=str.lower)) or "(none)"


def print_relays(config: dict[str, Any], path: Path | None) -> None:
    relays = relay_entries(config)
    rows = []
    for name, value in sorted(relays.items(), key=lambda item: str(item[0]).lower()):
        if not isinstance(value, dict):
            continue
        rows.append(
            {
                "name": name,
                "aliases": value.get("aliases", []),
                "base_url": value.get("base_url"),
                "model": value.get("model"),
                "api_key_env": value.get("api_key_env"),
            }
        )

    print(
        json.dumps(
            {
                "config": str(path or user_config_path()),
                "default": config.get("default"),
                "defaults": config.get("defaults", {}),
                "relays": rows,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def apply_config(args: argparse.Namespace, config: dict[str, Any], path: Path | None) -> None:
    defaults = config.get("defaults", {})
    if not isinstance(defaults, dict):
        defaults = {}

    relays = relay_entries(config)
    relay_name = args.relay or config.get("default")
    relay: dict[str, Any] = {}

    if relay_name:
        found_name, relay = find_relay(relays, str(relay_name))
        args.relay = found_name
    elif not args.base_url and len(relays) == 1:
        found_name, relay = next((str(name), value) for name, value in relays.items() if isinstance(value, dict))
        args.relay = found_name
    elif not args.base_url and relays:
        raise SystemExit(f"No relay was selected. Available relays: {format_relay_names(relays)}")

    if relay.get("base_url") and not args.base_url:
        args.base_url = normalize_base_url(str(relay["base_url"]))
    elif args.base_url:
        args.base_url = normalize_base_url(args.base_url)
    elif os.environ.get("OPENAI_BASE_URL"):
        args.base_url = normalize_base_url(os.environ["OPENAI_BASE_URL"])

    args.api_key_env = args.api_key_env or str(relay.get("api_key_env") or defaults.get("api_key_env") or DEFAULT_API_KEY_ENV)
    args.model = args.model or str(relay.get("model") or defaults.get("model") or DEFAULT_MODEL)
    args.size = normalize_size(args.size or str(relay.get("size") or defaults.get("size") or DEFAULT_SIZE))
    args.quality = args.quality or str(relay.get("quality") or defaults.get("quality") or DEFAULT_QUALITY)
    args.output_format = args.output_format or str(relay.get("output_format") or defaults.get("output_format") or DEFAULT_OUTPUT_FORMAT)
    args.timeout = float(args.timeout or relay.get("timeout") or defaults.get("timeout") or DEFAULT_TIMEOUT)
    args.config = str(path or user_config_path())

    if not args.base_url:
        raise SystemExit(f"No base URL configured. Add a relay first or pass --base-url. Config path: {args.config}")


def normalize_size(size: str) -> str:
    clean = size.strip().lower().replace("*", "x").replace("×", "x")
    clean = re.sub(r"\s+", "", clean)
    return SIZE_ALIASES.get(clean, clean)


def parse_dimensions(size: str) -> tuple[int, int] | None:
    match = re.fullmatch(r"(\d{2,5})x(\d{2,5})", normalize_size(size))
    if not match:
        return None
    width = int(match.group(1))
    height = int(match.group(2))
    if width < 64 or height < 64:
        return None
    return width, height


def fallback_size_for(size: str) -> str | None:
    dims = parse_dimensions(size)
    if not dims:
        return None
    width, height = dims
    ratio = width / height
    if ratio > 1.2:
        return FALLBACK_SIZES["landscape"]
    if ratio < 0.83:
        return FALLBACK_SIZES["portrait"]
    return FALLBACK_SIZES["square"]


def build_payload(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "model": args.model,
        "prompt": args.prompt,
        "size": normalize_size(args.size),
        "quality": args.quality,
        "output_format": args.output_format,
        "n": args.n,
    }


def output_paths(args: argparse.Namespace) -> list[Path]:
    ext = "jpg" if args.output_format == "jpeg" else args.output_format
    if args.out_dir:
        root = Path(args.out_dir)
        return [root / f"image_{index}.{ext}" for index in range(1, args.n + 1)]

    base = Path(args.out or f"output/imagegen/output.{ext}")
    if args.n == 1:
        return [base]

    suffix = base.suffix or f".{ext}"
    stem = base.stem if base.suffix else base.name
    parent = base.parent
    return [parent / f"{stem}_{index}{suffix}" for index in range(1, args.n + 1)]


def check_outputs(paths: list[Path], force: bool) -> None:
    for path in paths:
        if path.exists() and not force:
            raise SystemExit(f"Output already exists: {path}. Use --force to overwrite.")


def read_api_key(env_name: str) -> str | None:
    value = os.environ.get(env_name)
    if value:
        return value

    if os.name != "nt":
        return None

    try:
        import winreg  # type: ignore

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            value, _ = winreg.QueryValueEx(key, env_name)
    except OSError:
        return None

    return str(value) if value else None


def print_dry_run(args: argparse.Namespace, base_url: str, payload: dict[str, Any], paths: list[Path]) -> None:
    target_dims = parse_dimensions(payload["size"])
    dry_run = {
        "dry_run": True,
        "relay": args.relay,
        "config": args.config,
        "method": "POST",
        "url": f"{base_url}/images/generations",
        "api_key_env": args.api_key_env,
        "fallback_resize": bool(target_dims and not args.no_fallback_resize),
        "ensure_output_size": bool(target_dims and not args.no_ensure_output_size),
        "payload": payload,
        "outputs": [str(path) for path in paths],
    }
    print(json.dumps(dry_run, ensure_ascii=False, indent=2))


def request_generation(base_url: str, api_key: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url}/images/generations",
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "gpt-image-relay-skill",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise APIRequestError(f"Images API request failed with HTTP {exc.code}: {detail}", exc.code, detail) from exc
    except urllib.error.URLError as exc:
        raise APIRequestError(f"Images API request failed: {exc}") from exc


def download_url(url: str, timeout: float) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "gpt-image-relay-skill"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def decode_images(response: dict[str, Any], paths: list[Path], timeout: float) -> None:
    data = response.get("data")
    if not isinstance(data, list) or not data:
        raise SystemExit("Images API response did not include data[].")

    if len(data) > len(paths):
        raise SystemExit(f"Response returned {len(data)} images but only {len(paths)} output paths were prepared.")

    for item, path in zip(data, paths):
        if not isinstance(item, dict):
            raise SystemExit("Images API response data[] item was not an object.")

        if item.get("b64_json"):
            image_bytes = base64.b64decode(str(item["b64_json"]))
        elif item.get("url"):
            image_bytes = download_url(str(item["url"]), timeout)
        else:
            raise SystemExit("Images API response did not include b64_json or url output.")

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(image_bytes)
        print(f"Wrote {path}")


def resize_outputs(paths: list[Path], size: str, output_format: str) -> None:
    dims = parse_dimensions(size)
    if not dims:
        return

    try:
        from PIL import Image
    except ImportError as exc:
        raise SystemExit("Pillow is required to resize output images to exact dimensions.") from exc

    width, height = dims
    for path in paths:
        with Image.open(path) as image:
            resampling = getattr(getattr(Image, "Resampling", Image), "LANCZOS")
            resized = image.resize((width, height), resampling)
            if output_format == "jpeg" and resized.mode not in ("RGB", "L"):
                resized = resized.convert("RGB")
            resized.save(path)
        print(f"Resized {path} to {width}x{height}")


def main() -> int:
    args = parse_args()
    config, path = load_config(args)

    if args.list_relays:
        print_relays(config, path)
        return 0

    if not args.prompt:
        raise SystemExit("--prompt is required unless --list-relays is set.")

    apply_config(args, config, path)
    if args.n < 1 or args.n > 10:
        raise SystemExit("--n must be between 1 and 10.")

    base_url = normalize_base_url(args.base_url)
    payload = build_payload(args)
    paths = output_paths(args)
    check_outputs(paths, args.force)

    if args.dry_run:
        print_dry_run(args, base_url, payload, paths)
        return 0

    api_key = read_api_key(args.api_key_env)
    if not api_key:
        raise SystemExit(f"{args.api_key_env} is not set. Add/update the relay key locally or use --dry-run.")

    started = time.monotonic()
    target_size = payload["size"]
    fallback_used = False
    try:
        response = request_generation(base_url, api_key, payload, args.timeout)
    except APIRequestError as exc:
        fallback_size = fallback_size_for(target_size)
        if args.no_fallback_resize or not fallback_size or fallback_size == target_size:
            raise SystemExit(str(exc)) from exc

        fallback_payload = dict(payload)
        fallback_payload["size"] = fallback_size
        print(f"Initial size {target_size} failed; retrying with {fallback_size} then resizing output.")
        response = request_generation(base_url, api_key, fallback_payload, args.timeout)
        fallback_used = True

    decode_images(response, paths, args.timeout)
    if parse_dimensions(target_size) and (fallback_used or not args.no_ensure_output_size):
        resize_outputs(paths, target_size, args.output_format)

    elapsed = time.monotonic() - started
    print(f"Done in {elapsed:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
