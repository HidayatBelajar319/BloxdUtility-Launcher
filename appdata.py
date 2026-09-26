"""Shared storage helpers for the Players Launcher.

Everything the launcher writes at runtime (config, API keys, snippets, mods,
cached vocabulary) lives OUTSIDE the git repository, in a per-user data
directory. `config.json` never belongs in the repo and is git-ignored twice
(on purpose: here and in `.gitignore`).

Resolution order for the data directory:

1. `$BLOXD_LAUNCHER_HOME` (used by tests / portable installs)
2. the `dataDir` key of the config.json in the default location
3. `%APPDATA%\\BloxdUtilityLauncher` (Windows)
4. `~/.bloxdutility-launcher` (macOS / Linux)
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

APP_NAME = "BloxdUtilityLauncher"
APP_TITLE = "Players — Bloxd Utility Launcher"
VERSION = "1.0.0"
ENGINE_VERSION = "1.0.0"

HOME = Path(__file__).resolve().parent
ASSETS = HOME / "assets"
EDITOR_ASSETS = ASSETS / "editor"
TEMPLATES = HOME / "templates"
VENDOR_ENGINE = "BloxdUtilityCLI/editor (editor-core.js, bloxd-editor.js, bloxd-editor.css)"

LINKS = {
    "site": "https://bloxdutility.netlify.app/",
    "docs": "https://bloxdutility-documentation.netlify.app/",
    "codeApi": "https://github.com/Bloxdy/code-api",
    "texturePacks": "https://github.com/Bloxdy/texture-packs",
    "wiki": "https://bloxd-io.fandom.com/wiki/Code_Block",
    "bloxd": "https://bloxd.io",
    "launcher": "https://github.com/HidayatBelajar319/BloxdUtility-Launcher",
    "owner": "https://github.com/FallenNightA",
    "official": "https://github.com/HidayatBelajar319",
}

DEFAULTS = {
    "dataDir": "",
    "bloxdUrl": LINKS["bloxd"],
    "docsUrl": LINKS["docs"],
    "aiProvider": "auto",
    "openrouterKey": "",
    "openrouterModel": "openrouter/free",
    "groqKey": "",
    "groqModel": "llama-3.3-70b-versatile",
    "pollinationsKey": "",
    "pollinationsModel": "openai/gpt-4o-mini",
    "editorDebounceMs": 200,
    "lastSnippet": "",
}

SECRET_FIELDS = ("openrouterKey", "groqKey", "pollinationsKey")

# Every provider needs the user's own key. There is deliberately NO anonymous
# default: Pollinations without a key answers HTTP 200 with ENOSPC / budget
# payloads, which is worse than an honest error.
PROVIDERS = {
    "openrouter": {
        "id": "openrouter",
        "label": "OpenRouter",
        "endpoint": "https://openrouter.ai/api/v1/chat/completions",
        "keyField": "openrouterKey",
        "modelField": "openrouterModel",
        "keyHelp": "Free key: https://openrouter.ai/keys",
    },
    "groq": {
        "id": "groq",
        "label": "Groq",
        "endpoint": "https://api.groq.com/openai/v1/chat/completions",
        "keyField": "groqKey",
        "modelField": "groqModel",
        "keyHelp": "Free key: https://console.groq.com/keys",
    },
    "pollinations": {
        "id": "pollinations",
        "label": "Pollinations",
        "endpoint": "https://gen.pollinations.ai/v1/chat/completions",
        "keyField": "pollinationsKey",
        "modelField": "pollinationsModel",
        "keyHelp": "Key must start with pk_ or sk_ — https://pollinations.ai",
    },
}
PROVIDER_ORDER = ("openrouter", "groq", "pollinations")


# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #

def default_data_dir() -> Path:
    env = os.environ.get("BLOXD_LAUNCHER_HOME", "").strip()
    if env:
        return Path(env).expanduser()
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / APP_NAME
    return Path.home() / ".bloxdutility-launcher"


def anchor_config_path() -> Path:
    """The config.json in the default location; it may point at a custom dataDir."""
    return default_data_dir() / "config.json"


def data_dir() -> Path:
    env = os.environ.get("BLOXD_LAUNCHER_HOME", "").strip()
    if env:
        return Path(env).expanduser()
    anchor = anchor_config_path()
    if anchor.is_file():
        pointed = read_config(anchor).get("dataDir", "")
        if pointed:
            candidate = Path(str(pointed)).expanduser()
            if candidate.resolve() != default_data_dir().resolve():
                return candidate
    return default_data_dir()


def config_path() -> Path:
    return data_dir() / "config.json"


def snippets_dir() -> Path:
    return data_dir() / "snippets"


def mods_dir() -> Path:
    return data_dir() / "mods"


def docs_dir() -> Path:
    return data_dir() / "docs"


def cache_dir() -> Path:
    return data_dir() / "cache"


def all_dirs() -> tuple:
    return (data_dir(), snippets_dir(), mods_dir(), docs_dir(), cache_dir())


def ensure_dirs() -> None:
    for folder in all_dirs():
        folder.mkdir(parents=True, exist_ok=True)


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #

def read_config(path: Path | None = None) -> dict:
    target = Path(path) if path else config_path()
    try:
        parsed = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def write_config(patch: dict, path: Path | None = None) -> Path:
    target = Path(path) if path else config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    merged = dict(read_config(target))
    merged.update(patch or {})
    target.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(target, 0o600)
    except OSError:
        pass  # best effort, Windows ignores POSIX modes
    return target


def config() -> dict:
    merged = dict(DEFAULTS)
    merged.update(read_config())
    return merged


def set_data_dir(new_dir: str) -> Path:
    """Point the launcher at another data directory and move the config over."""
    target = Path(new_dir).expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)
    for folder in (target, target / "snippets", target / "mods", target / "docs", target / "cache"):
        folder.mkdir(parents=True, exist_ok=True)

    payload = {k: v for k, v in config().items() if k != "dataDir"}
    (target / "config.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    anchor = anchor_config_path()
    anchor.parent.mkdir(parents=True, exist_ok=True)
    anchor.write_text(json.dumps({"dataDir": str(target)}, indent=2) + "\n", encoding="utf-8")
    return target


def mask_key(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return "(not set)"
    if len(text) <= 10:
        return f"{text[:2]}****"
    return f"{text[:6]}...{text[-4:]} ({len(text)} chars)"


def looks_like_pollinations_key(value: str) -> bool:
    return bool(re.match(r"^(pk_|sk_)", str(value or "").strip()))


# --------------------------------------------------------------------------- #
# Snippets
# --------------------------------------------------------------------------- #

SAFE_NAME = re.compile(r"[^A-Za-z0-9._ -]+")


def safe_snippet_name(name: str, fallback: str = "snippet") -> str:
    cleaned = SAFE_NAME.sub("_", str(name or "").strip()).strip(" ._")
    cleaned = cleaned or fallback
    if cleaned.lower().endswith(".js"):
        cleaned = cleaned[:-3]
    return cleaned[:80]


def list_snippets() -> list:
    folder = snippets_dir()
    folder.mkdir(parents=True, exist_ok=True)
    out = []
    for item in sorted(folder.glob("*.js"), key=lambda p: p.name.lower()):
        try:
            stat = item.stat()
        except OSError:
            continue
        out.append({"name": item.stem, "path": str(item), "bytes": stat.st_size,
                    "modified": stat.st_mtime})
    return out


def read_snippet(name: str) -> str:
    return snippet_path(name).read_text(encoding="utf-8")


def write_snippet(name: str, source: str) -> str:
    target = snippet_path(name)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source or "", encoding="utf-8")
    return str(target)


def delete_snippet(name: str) -> bool:
    target = snippet_path(name)
    try:
        target.unlink()
        return True
    except OSError:
        return False


def snippet_path(name: str) -> Path:
    return snippets_dir() / f"{safe_snippet_name(name)}.js"


# --------------------------------------------------------------------------- #
# Mods
# --------------------------------------------------------------------------- #

def list_mods() -> list:
    """Installed mod folders and loose files (zips included) in the mods dir."""
    folder = mods_dir()
    folder.mkdir(parents=True, exist_ok=True)
    out = []
    for item in sorted(folder.iterdir(), key=lambda p: p.name.lower()):
        try:
            stat = item.stat()
        except OSError:
            continue
        if item.is_dir():
            total = 0
            for child in item.rglob("*"):
                if child.is_file():
                    try:
                        total += child.stat().st_size
                    except OSError:
                        continue
            out.append({"name": item.name, "path": str(item), "bytes": total,
                        "isZip": False, "isFolder": True, "modified": stat.st_mtime})
        else:
            out.append({"name": item.name, "path": str(item), "bytes": stat.st_size,
                        "isZip": item.suffix.lower() == ".zip", "isFolder": False,
                        "modified": stat.st_mtime})
    return out


def install_mod(archive: str) -> dict:
    """Unpack a .zip into mods/<name>/. Paths that escape the target are skipped."""
    import zipfile

    source = Path(archive)
    if not source.is_file():
        return {"ok": False, "message": f"No such file: {source}"}
    if source.suffix.lower() != ".zip":
        return {"ok": False, "message": "Only .zip mod archives can be installed."}

    target = mods_dir() / safe_snippet_name(source.stem, "mod")
    try:
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(source) as zf:
            members = [name for name in zf.namelist()
                       if not name.endswith("/")
                       and str((target / name).resolve()).startswith(str(target.resolve()))]
            zf.extractall(target, members=members)
        return {"ok": True, "message": f"Installed {len(members)} file(s) to {target}", "path": str(target)}
    except zipfile.BadZipFile:
        return {"ok": False, "message": "That file is not a valid .zip archive."}
    except OSError as err:
        return {"ok": False, "message": f"Install failed: {err}"}


# --------------------------------------------------------------------------- #
# Vocabulary cache (fed by the vendored editor engine)
# --------------------------------------------------------------------------- #

def vocab_cache_path() -> Path:
    return cache_dir() / "vocabulary.json"


def read_vocab_cache() -> dict:
    try:
        return json.loads(vocab_cache_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def write_vocab_cache(vocab: dict) -> bool:
    if not isinstance(vocab, dict):
        return False
    target = vocab_cache_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        target.write_text(json.dumps(vocab), encoding="utf-8")
    except (OSError, TypeError):
        return False
    return True


def http_get(url: str, timeout: int = 20) -> dict:
    """Fetcher for the editor engine. Returns {ok, status, text} — never raises."""
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/vnd.github+json, text/plain, */*",
                 "User-Agent": f"{APP_NAME}/{VERSION}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            return {"ok": 200 <= response.status < 300,
                    "status": response.status,
                    "text": raw.decode("utf-8", "replace")}
    except urllib.error.HTTPError as err:
        body = ""
        try:
            body = err.read().decode("utf-8", "replace")
        except Exception:  # pragma: no cover - defensive
            pass
        return {"ok": False, "status": err.code, "text": body}
    except Exception as err:  # network down, DNS, TLS, timeout...
        return {"ok": False, "status": 0, "text": f"{type(err).__name__}: {err}"}


def requests_available() -> bool:
    try:
        import requests  # noqa: F401
        return True
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# Misc
# --------------------------------------------------------------------------- #

def open_in_browser(url: str) -> bool:
    try:
        import webbrowser
        return bool(webbrowser.open(url))
    except Exception:
        return False


def human_bytes(count: int) -> str:
    value = float(count or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def iso_time(stamp: float) -> str:
    if not stamp:
        return "—"
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(stamp))


def vendor_engine_present() -> bool:
    return all((EDITOR_ASSETS / name).is_file()
               for name in ("editor-core.js", "bloxd-editor.js", "bloxd-editor.css"))


def engine_version() -> str:
    """Read core.VERSION straight out of the vendored file (no JS runtime)."""
    try:
        text = (EDITOR_ASSETS / "editor-core.js").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "missing"
    match = re.search(r"const\s+VERSION\s*=\s*'([^']+)'", text)
    return match.group(1) if match else "unknown"


def list_templates() -> list:
    if not TEMPLATES.is_dir():
        return []
    out = []
    for item in sorted(TEMPLATES.glob("*.js"), key=lambda p: p.name.lower()):
        try:
            out.append({"name": item.stem, "path": str(item), "source": item.read_text(encoding="utf-8")})
        except OSError:
            continue
    return out


def stats() -> dict:
    snippets = list_snippets()
    mods = list_mods()
    zips = [m for m in mods if m["isZip"]]
    try:
        import tkinter
        tk_version = f"Tk {tkinter.TkVersion}"
    except Exception:
        tk_version = "unavailable"
    try:
        import webview  # noqa: F401
        webview_state = "available"
    except Exception:
        webview_state = "missing (pip install pywebview)"
    return {
        "version": VERSION,
        "engine": engine_version(),
        "dataDir": str(data_dir()),
        "snippets": len(snippets),
        "snippetBytes": sum(s["bytes"] for s in snippets),
        "mods": len(mods),
        "modZips": len(zips),
        "templates": len(list_templates()),
        "vendorEngine": "present" if vendor_engine_present() else "missing",
        "python": sys.version.split()[0],
        "tk": tk_version,
        "pywebview": webview_state,
        "requests": "available" if requests_available() else "missing",
        "vocabCache": "present" if vocab_cache_path().is_file() else "empty",
    }


def relative_to_data(path: str) -> str:
    try:
        return str(Path(path).relative_to(data_dir()))
    except (ValueError, TypeError):
        return str(path)
