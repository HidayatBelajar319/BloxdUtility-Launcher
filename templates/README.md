# templates/ — player-control templates

Bloxd.js building blocks shipped with the launcher, in the AI panel sidebar
(**Copy** / **Open in Lab** / **Ask AI**).

| File | What it gives you |
| --- | --- |
| `equip_item.js` | Give items, hot-swap a loadout, report inventory failures. |
| `attack_target.js` | Cooldown-gated hits, nearest-target search, lunge impulse. |
| `move_player.js` | Walk / sprint / jump / stop, and step toward a position. |
| `account_player_data.js` | Per-player coins, score, progress, settings + save on close. |
| `feature_toggles.js` | `pvp` / `doubleJump` / `noclip` / `flySpeed` flags with chat commands. |

## Status — Phase-2 foundation, honestly

These files are **the foundation for a real-player agent, not an agent**. The
launcher v1 does not connect them to anything live: no input loop, no
perception, no memory, no autonomous play. Today you paste them into World
Code (F8) or load them into the Code Lab, and you or a player triggers them.

The next phase wires the same functions to an in-game controller — that is the
only thing missing, and nothing here needs rewriting for it.

## Rules the templates follow

* No `//` comments (Bloxd forbids them) — `/* block comments */` only.
* All shared state lives on `globalThis`, because `let`/`const` are invisible
  to Code Blocks.
* `tick` is 20x/second, so the hot paths are cheap and cooldown-gated.
* Every `api.*` call is checked against the Code Lab vocabulary; the problems
  panel warns on names it does not know. A warning means "verify this function
  name", not "the template is broken".
