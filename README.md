# Players — Bloxd Utility Launcher

A light desktop hub for Bloxd Utility: play Bloxd.io, write Bloxd code in the
real Bloxd Editor, ask the AI for gameplay/coding help, and keep your mods and
snippets on your own disk.

* Python 3.10+
* `tkinter` (ships with Python on Windows/macOS; on Debian/Ubuntu:
  `sudo apt install python3-tk`)
* Three small pip packages — no Node, no bundler, no Electron.

## Install

```bash
cd BloxdUtilityLauncher
python -m venv venv
venv\Scripts\activate          # Windows
# source venv/bin/activate     # macOS / Linux
pip install -r requirements.txt
```

## Run

```bash
python launcher.py
```

Useful flags:

```bash
python launcher.py --version          # version + vendored engine version
python launcher.py --data-dir D:\bu   # use another data folder for this run
python game.py                        # just the Bloxd.io player window
python game.py https://bloxd.io       # player window on a specific URL
```

## Tabs

| Tab | What it does |
| --- | --- |
| **Home** | Play / Code Lab / AI shortcuts, project links, live stats, tips. |
| **Code Lab** | The vendored Bloxd Editor in a pywebview window: syntax highlighting, custom Bloxd-API autocomplete, problems panel, debounced real-time `trainReport`, plus a snippet file manager (new / save / save-as / rename / delete / copy) for the files in your data folder. |
| **AI** | Chat with a key-required provider chain (OpenRouter → Groq → Pollinations), "Save code as snippet" on the last fenced block, "Insert into Code Lab", quick prompts, and the `templates/` player-control folder with copy buttons. |
| **Mods** | The local mods folder: list, install `.zip` archives, delete, reveal. |
| **Docs** | Open the docs website, or read local markdown offline (added through "Add markdown file…", plus the bundled README/CHANGELOG and `templates/README.md`). |
| **Changelog** | What shipped in this launcher, plus links to the site changelogs. |
| **Settings** | Data folder, AI keys + models, Bloxd.io URL, docs URL, editor debounce. |

## Where your data lives

Nothing is written inside the repository. Everything goes to a per-user data
directory:

* Windows — `%APPDATA%\BloxdUtilityLauncher`
* macOS / Linux — `~/.bloxdutility-launcher`
* override per run with `BLOXD_LAUNCHER_HOME` or `--data-dir`

```
<data dir>/
├── config.json        # settings + AI keys (git-ignored, chmod 600 where supported)
├── snippets/          # Code Lab snippet files (*.js)
├── mods/              # installed mods and .zip archives
├── docs/              # markdown you added for the Docs tab
└── cache/             # cached Bloxd API vocabulary
```

## The vendored editor engine

`assets/editor/` holds a **copy** of the shared Bloxd Editor engine — no
symlink, no import across repositories, so the launcher works on its own:

| File | Origin |
| --- | --- |
| `assets/editor/editor-core.js` | `BloxdUtilityCLI/editor/editor-core.js` |
| `assets/editor/bloxd-editor.js` | `BloxdUtilityCLI/editor/bloxd-editor.js` |
| `assets/editor/bloxd-editor.css` | `BloxdUtilityCLI/editor/bloxd-editor.css` |

* Vendored version: **engine v1.0.0** (frozen contract v1.0.0 — see
  `BloxdUtilityCLI/editor/README.md` for the exported names and return shapes).
* The copies are byte-identical to the CLI originals. To re-sync after an
  upstream change, copy the three files over and bump the version in the README
  below. Do not hand-edit them: the CLI depends on the same contract.
* Autocomplete is not Monaco's — the vocabulary is parsed live from
  `Bloxdy/code-api`, cached in `<data dir>/cache/vocabulary.json`, and falls
  back to the built-in list when you are offline.

## AI keys are required

There is deliberately **no anonymous provider**. Pollinations without your own
key answers HTTP 200 with ENOSPC / budget payloads, which is worse than an
honest error, so the panel refuses to talk until you paste a key in Settings:

* OpenRouter — <https://openrouter.ai/keys>
* Groq — <https://console.groq.com/keys>
* Pollinations — needs your own `pk_`/`sk_` key, <https://pollinations.ai>

Keys are written to `config.json` in the data folder, shown masked, and
git-ignored. `BUCLI_KEY` is not read — the launcher never touches the CLI's
config.

### AI-as-player status — Phase-2 foundation, honestly

The AI panel is a **code** assistant today. It can write the account, movement
and feature-control code a real in-game player would run, and `templates/`
ships ready-made versions of exactly that. What it does **not** do yet: drive a
live game. There is no input loop, no perception, no memory and no autonomous
play in v1. The templates and quick prompts are the foundation Phase 2 wires a
controller onto — nothing in them needs rewriting for that.

## Known limits

* The Code Lab webview runs `webview.start()` on a background thread so the hub
  keeps repainting. On some Windows setups pywebview prefers the main thread;
  if the window misbehaves, close it and reopen, or open
  `assets/code_lab.html` directly in a browser (the editor still works, the
  file-manager bridge does not).
* The Play button launches `game.py` as a **separate process** so the Code Lab
  webview session and the hub are never disturbed.
* AI responses are non-streaming (one request per provider, with the chain
  falling through on failure) — no token-by-token typing yet.
* Templates only use API calls that are in the Bloxd vocabulary; the Code Lab
  problems panel warns about any `api.*` name it does not recognise.

## Credits

* **Bloxd Editor engine** — `editor-core.js`, `bloxd-editor.js`,
  `bloxd-editor.css`, vendored from `BloxdUtilityCLI/editor` (owner:
  <https://github.com/FallenNightA>). Autocomplete vocabulary from
  [Bloxdy/code-api](https://github.com/Bloxdy/code-api).
* **Original project owner** — [FallenNightA](https://github.com/FallenNightA).
* **Official Bloxd Utility organisation** —
  <https://github.com/HidayatBelajar319>. This launcher is an
  **official-by-owner notice**: it is published by / for the owner's
  organisation, and the owner, FallenNightA, is credited above as the original
  author. Please keep that attribution when you fork or redistribute.
* Bloxd.io itself and its Code Block runtime belong to their respective owners;
  this project is not affiliated with or endorsed by Bloxd.io.
