# Changelog

All notable changes to the Players Launcher. This project follows the style of
the Utility site and the Docs site.

## v1.0.0 — 2026-09-26

First runnable desktop release.

### Added

* **Code Lab** — the shared Bloxd Editor engine vendored into
  `assets/editor/` (`editor-core.js`, `bloxd-editor.js`, `bloxd-editor.css`),
  byte-identical to the `BloxdUtilityCLI/editor` copies, engine v1.0.0.
  Syntax highlighting, custom Bloxd-API autocomplete from the `Bloxdy/code-api`
  vocabulary, problems panel, and debounced real-time `trainReport` shown
  in-app. Snippet file manager (new / save / save as / rename / delete / copy)
  over the files in the data folder.
* **AI panel** — chat with a key-required provider chain
  (OpenRouter → Groq → Pollinations), non-streaming, with per-provider failure
  reporting. Fenced-code "Save as snippet" and "Insert into Code Lab" buttons,
  plus quick prompts for account, movement, equip, attack, feature flags and
  debugging.
* **Templates** — `templates/`: `equip_item.js`, `attack_target.js`,
  `move_player.js`, `account_player_data.js`, `feature_toggles.js`, each with
  copy buttons in the AI panel. Phase-2 foundation for the in-game AI player.
* **Player window** — `game.py`, an embedded Bloxd.io window via pywebview,
  launched from the Play button in its own process.
* **Hub tabs** — Home (links + live stats + tips), Mods (list / install `.zip` /
  delete), Docs (open the docs site or read local markdown), Changelog, and
  Settings (data folder, AI keys and models, Bloxd.io URL, docs URL, editor
  debounce).
* Per-user data directory with `config.json`, `snippets/`, `mods/`, `docs/` and
  `cache/`; nothing is written inside the repository.

### Notes

* No anonymous AI provider on purpose — Pollinations without your own key is
  dead (HTTP 200 with ENOSPC / budget payloads), so keys are required.
* The AI-as-player body is **Phase-2 foundation only**: templates and prompts,
  no live input loop, no perception, no memory, no autonomous play.
