"""Players Launcher — AI panel (chat, provider chain, code hand-off, templates).

Two jobs, on purpose:

1. **Coding help** — ask for Bloxd.io code in plain language, then push the
   fenced ``` block straight into the Code Lab as a snippet.
2. **Gameplay / player foundation (Phase 2)** — prompts and templates for the
   account, movement and feature-control code a real player needs.

HONEST PHASE-2 STATUS
---------------------
The AI is a *code* assistant right now. It can produce the account, movement
and feature-control code that a future in-game agent will run, and the
`templates/` folder ships ready-made versions of that code — but **nothing is
wired to a live game input loop yet**: no memory, no perception, no autonomous
play, no live player control. Treat everything here as the foundation Phase 2
builds on, not as a working game bot.

KEY HANDLING
------------
There is deliberately no anonymous default. Pollinations without your own key
answers HTTP 200 with ENOSPC / budget payloads, which is worse than an honest
error, so every provider needs a key the user pastes in Settings. Keys are
stored in `config.json` inside the per-user data directory (never in the repo)
and are only ever displayed masked.
"""

from __future__ import annotations

import json
import queue
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import appdata  # noqa: E402

import tkinter as tk  # noqa: E402
from tkinter import messagebox, ttk  # noqa: E402

SYSTEM_PROMPT = (
    "You are the Bloxd.io scripting assistant inside Bloxd Utility — a code assistant that "
    "writes, explains and debugs Bloxd.io scripts for a player who only copy-pastes.\n\n"
    "Output rules (mandatory):\n"
    "- Always return runnable code inside a fenced code block tagged with its language "
    "(for example ```javascript). No untagged code, ever.\n"
    "- Ship complete, working code. Never leave placeholder stubs or TODO markers.\n"
    "- Keep the prose outside the fence short: what the code does, and where to paste it.\n"
    "- If a Bloxd.io API signature is uncertain, say so explicitly instead of inventing one.\n\n"
    "Bloxd.io engine rules you must respect:\n"
    "// line comments DO NOT WORK — use /* block comments */.\n"
    "myId, playerId, thisPos and ownerDbId are provided by the engine.\n"
    "World Code runs ONCE (F8 on PC) and holds every callback and globalThis variable; "
    "Code Blocks are limited to 500 lines / 16000 characters.\n"
    "tick fires 20x per second — keep it cheap.\n"
    "Block and item names are case-sensitive catalog names.\n"
    "Never invent a function, callback, block or item name that you cannot justify."
)

PHASE2_NOTE = (
    "Phase-2 foundation (honest): the AI writes account / movement / feature-control code "
    "and the templates ship it, but there is NO live game input loop yet — no perception, "
    "no memory, no autonomous play. Nothing here controls a running game."
)

QUICK_PROMPTS = [
    ("Account", "Write Bloxd.io code that stores per-player data (score, coins, progress) in "
                "globalThis from World Code, saves it on close and loads it on join. Player-data only."),
    ("Movement", "Write Bloxd.io code for a movement helper: forward walk, jump, sprint and a "
                 "stop, using only documented api.* calls. Explain the signature you rely on."),
    ("Equip", "Write Bloxd.io code that equips, hot-swaps and removes a hotbar item for the "
              "current player, and say which api functions it needs."),
    ("Attack", "Write Bloxd.io code for an attack helper: cooldowns, a hitbox check against a "
               "target player id, and feedback via api.sendMessage."),
    ("Feature flags", "Write Bloxd.io code for runtime feature toggles (features.pvp, features.doubleJump, "
                      "features.noclip) read from player data, with a chat command to flip them."),
    ("Debug", "Here is my Bloxd.io script and the problems panel says X. Diagnose and return the fixed "
              "version in one fenced javascript block."),
]

FENCE_RE = re.compile(r"```([A-Za-z0-9_+#.-]*)[ \t]*\r?\n([\s\S]*?)```")

HARD_ERROR_PATTERNS = [re.compile(p, re.I) for p in (
    r"enospc", r"no\s*space\s*left", r"\b500\s+internal\s+server\s+error\b",
)]
BUDGET_ERROR_PATTERNS = [re.compile(p, re.I) for p in (
    r"enospc", r"no\s*space\s*left", r"out\s*of\s*(budget|credit|quota|funds)",
    r"budget\s*(exhausted|exceeded|depleted|reached|used\s*up|error)",
    r"shared\s*(budget|pool|quota)\s*(is\s*)?(exhausted|depleted|empty)",
    r"insufficient[_\s-]*(quota|credit|balance|funds)",
    r"quota\s*(exhausted|exceeded|reached)", r"rate[_\s-]*limit",
    r"payment\s+required", r"too\s+many\s+requests", r"exceeded\s+your\s+current\s+quota",
)]
ERROR_ENVELOPE_PATTERNS = [re.compile(p, re.I) for p in (
    r"^\s*\{\s*\"?error\"?\s*:", r"^\s*\{\s*\"?message\"?\s*:\s*\"(?:error|failed)",
    r"^\s*\{\s*\"object\"\s*:\s*\"error", r"^Error:\s",
)]
LANGUAGE_ALIASES = {"js": "javascript", "jsx": "javascript", "mjs": "javascript",
                    "cjs": "javascript", "ts": "typescript", "py": "python", "sh": "bash"}

STALE_PAYLOAD_MARKER = "chatcmpl-830a5120750463f9"


def normalize_language(lang: str) -> str:
    key = str(lang or "").strip().lower()
    if not key:
        return ""
    return LANGUAGE_ALIASES.get(key, key)


def looks_like_provider_error(text: str, is_code_block: bool = False) -> bool:
    """True when a payload is a service failure rather than an answer."""
    if not text:
        return False
    body = str(text)
    if STALE_PAYLOAD_MARKER in body:
        return True
    trimmed = body.strip()
    if not trimmed:
        return False
    if any(re.search(trimmed) for re in HARD_ERROR_PATTERNS):
        return True
    if any(re.search(trimmed) for re in BUDGET_ERROR_PATTERNS):
        if is_code_block:
            return True
        if len(trimmed) > 1200:
            return False
        if "```" in trimmed:
            return False
        return True
    if len(trimmed) < 600:
        for pattern in ERROR_ENVELOPE_PATTERNS:
            if pattern.match(trimmed):
                return True
    return False


def extract_fenced_code(text: str, prefer_largest: bool = True):
    """Return {'code', 'lang'} for the best fenced block, or None."""
    if not text:
        return None
    blocks = []
    for match in FENCE_RE.finditer(str(text)):
        code = (match.group(2) or "").strip()
        if len(code) >= 3 and not looks_like_provider_error(code, is_code_block=True):
            blocks.append({"code": code, "lang": normalize_language(match.group(1))})
    if not blocks:
        return None
    if prefer_largest:
        return max(blocks, key=lambda b: len(b["code"]))
    return blocks[-1]


class AiError(Exception):
    def __init__(self, provider: str, message: str, hint: str = ""):
        super().__init__(message)
        self.provider = provider
        self.hint = hint


def _post_json(url: str, headers: dict, payload: dict, timeout: int) -> dict:
    body = json.dumps(payload).encode("utf-8")
    if appdata.requests_available():
        import requests  # local import: optional dependency

        response = requests.post(url, headers=headers, data=body, timeout=timeout)
        return {"status": response.status_code, "text": response.text}
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return {"status": response.status, "text": response.read().decode("utf-8", "replace")}
    except urllib.error.HTTPError as err:
        return {"status": err.code, "text": err.read().decode("utf-8", "replace")}


def _extract_completion_text(payload) -> str:
    if isinstance(payload, str):
        return payload
    if not isinstance(payload, dict):
        return ""
    candidates = [
        payload.get("text"),
        (payload.get("message") or {}).get("text") if isinstance(payload.get("message"), dict) else None,
        (payload.get("message") or {}).get("content") if isinstance(payload.get("message"), dict) else None,
        (((payload.get("choices") or [{}])[0] or {}).get("message") or {}).get("content")
        if payload.get("choices") else None,
        (((payload.get("choices") or [{}])[0] or {}).get("text")) if payload.get("choices") else None,
        payload.get("content"),
    ]
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return candidate
    return ""


def resolve_chain(settings: dict):
    """Ordered provider list. Every entry needs its own key — no anonymous default."""
    wanted = str(settings.get("aiProvider") or "auto").strip().lower()
    chain = []
    if wanted != "auto":
        if wanted not in appdata.PROVIDERS:
            raise AiError("launcher", f"Unknown provider '{wanted}'. Valid: "
                                      f"{', '.join(appdata.PROVIDER_ORDER)}, auto.")
        if not str(settings.get(appdata.PROVIDERS[wanted]["keyField"]) or "").strip():
            raise AiError("launcher", f"No {appdata.PROVIDERS[wanted]['label']} key saved in Settings.",
                          hint=f"Get one: {appdata.PROVIDERS[wanted]['keyHelp']}")
        if wanted == "pollinations" and not appdata.looks_like_pollinations_key(
                settings.get("pollinationsKey", "")):
            raise AiError("Pollinations",
                          "Pollinations needs your own key starting with 'pk_' or 'sk_'.",
                          hint="Get one at https://pollinations.ai and save it in Settings.")
        chain = [wanted]
    else:
        chain = [pid for pid in appdata.PROVIDER_ORDER
                 if str(settings.get(appdata.PROVIDERS[pid]["keyField"]) or "").strip()]
    if "pollinations" in chain and not appdata.looks_like_pollinations_key(
            settings.get("pollinationsKey", "")):
        chain = [pid for pid in chain if pid != "pollinations"]
    if not chain:
        raise AiError("launcher", "No usable AI key configured.", hint=(
            "Open Settings and paste a key for OpenRouter (https://openrouter.ai/keys), "
            "Groq (https://console.groq.com/keys) or Pollinations (pk_/sk_). "
            "There is no anonymous provider — Pollinations without your own key is dead."))
    return chain


def request_completion(messages, settings, on_provider=None, timeout: int = 90):
    """Run the messages down the key-required chain, return the first real answer."""
    cleaned = [m for m in messages if m and str(m.get("content", "")).strip()]
    if not cleaned:
        raise AiError("launcher", "Nothing to send to the AI.")

    chain = resolve_chain(settings)
    failures = []
    for provider_id in chain:
        provider = appdata.PROVIDERS[provider_id]
        key = str(settings.get(provider["keyField"]) or "").strip()
        if not key:
            failures.append((provider["label"], "no key saved in Settings"))
            continue
        if provider_id == "pollinations" and not appdata.looks_like_pollinations_key(key):
            failures.append((provider["label"], "needs your own key starting with pk_ or sk_"))
            continue

        model = str(settings.get(provider["modelField"]) or "").strip() or "openrouter/free"
        if on_provider:
            on_provider(provider["label"], model)

        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {key}"}
        if provider_id == "openrouter":
            headers["HTTP-Referer"] = appdata.LINKS["launcher"]
            headers["X-Title"] = "Bloxd Utility Launcher"
        elif provider_id == "pollinations":
            headers["api-key"] = key
            headers["Referer"] = appdata.LINKS["site"]

        try:
            result = _post_json(provider["endpoint"], headers,
                                {"model": model, "messages": cleaned, "stream": False},
                                timeout)
            status = int(result.get("status") or 0)
            raw = result.get("text") or ""
            if status < 200 or status >= 300:
                hint = ""
                if status in (401, 403):
                    hint = "The provider rejected the key — re-save it in Settings."
                elif status == 402:
                    hint = "Out of credits on this provider — switch provider in Settings."
                elif status == 429:
                    hint = "Rate limited — wait a moment or switch provider."
                failures.append((provider["label"],
                                 f"HTTP {status}: {raw[:180]}", hint))
                continue
            try:
                parsed = json.loads(raw)
            except ValueError:
                parsed = raw
            if isinstance(parsed, dict) and parsed.get("error"):
                err = parsed["error"]
                message = err if isinstance(err, str) else (err or {}).get("message", json.dumps(err))
                failures.append((provider["label"], f"error: {str(message)[:200]}"))
                continue
            text = _extract_completion_text(parsed).strip()
            if not text:
                failures.append((provider["label"], "empty response"))
                continue
            if looks_like_provider_error(text):
                failures.append((provider["label"],
                                 "answered with a service error (budget/ENOSPC) instead of a reply"))
                continue
            return {"text": text, "provider": provider["label"], "model": model}
        except Exception as err:  # network, TLS, timeout...
            failures.append((provider["label"], f"{type(err).__name__}: {err}"))

    lines = [f"  - {label}: {message}" for label, message, *_ in failures]
    hints = [f"  hint: {h}" for _, _, *rest in failures for h in rest if h]
    raise AiError("launcher", "Every configured provider failed:\n" + "\n".join(lines + hints))


class AiPanel(ttk.Frame):
    """Chat + keys + templates, embedded as one tab of the hub."""

    def __init__(self, parent, app):
        super().__init__(parent, padding=8)
        self.app = app
        self.history = []          # [{role, content}]
        self.results = queue.Queue()
        self.busy = False
        self._provider_model = ""

        self._build_ui()
        self._load_settings()
        self._say("assistant",
                  "Keys are required — set one in Settings (or the key box on the right) before "
                  "the first question.\n" + PHASE2_NOTE)
        self.after(120, self._poll)

    # -- UI ---------------------------------------------------------------- #
    def _build_ui(self) -> None:
        paned = ttk.PanedWindow(self, orient="horizontal")
        paned.pack(fill="both", expand=True)

        left = ttk.Frame(paned, padding=(0, 0, 8, 0))
        right = ttk.Frame(paned, width=320)
        paned.add(left, weight=4)
        paned.add(right, weight=1)

        transcript = tk.Frame(left)
        transcript.pack(fill="both", expand=True)
        scroll = ttk.Scrollbar(transcript, orient="vertical")
        scroll.pack(side="right", fill="y")
        self.chat = tk.Text(transcript, wrap="word", state="disabled", height=18,
                            font=("Consolas", 10), background="#12151c", foreground="#dfe6f2",
                            insertbackground="#dfe6f2", relief="flat",
                            highlightthickness=1, highlightbackground="#2a3141")
        self.chat.pack(side="left", fill="both", expand=True)
        self.chat.configure(yscrollcommand=scroll.set)
        scroll.configure(command=self.chat.yview)
        self.chat.tag_configure("you", foreground="#7fb2ff")
        self.chat.tag_configure("assistant", foreground="#dfe6f2")
        self.chat.tag_configure("meta", foreground="#8a94a8")
        self.chat.tag_configure("error", foreground="#e05a5a")
        self.chat.tag_configure("code", foreground="#9fd0ff", lmargin1=14, lmargin2=14)

        composer = ttk.Frame(left)
        composer.pack(fill="x", pady=(8, 0))
        self.input = tk.Text(composer, height=4, wrap="word", font=("Consolas", 10),
                             background="#12151c", foreground="#dfe6f2", insertbackground="#dfe6f2",
                             relief="flat", highlightthickness=1, highlightbackground="#2a3141")
        self.input.pack(side="left", fill="both", expand=True)
        buttons = ttk.Frame(composer)
        buttons.pack(side="right", fill="y", padx=(8, 0))
        ttk.Button(buttons, text="Send", command=self.send).pack(fill="x")
        ttk.Button(buttons, text="Clear", command=self.clear_chat).pack(fill="x", pady=4)
        self.btn_save_snip = ttk.Button(buttons, text="Save code as snippet",
                                        command=self.save_last_code_as_snippet)
        self.btn_save_snip.pack(fill="x")
        self.btn_copy = ttk.Button(buttons, text="Copy last code", command=self.copy_last_code)
        self.btn_copy.pack(fill="x", pady=4)
        ttk.Button(buttons, text="Insert into Code Lab", command=self.insert_into_lab).pack(fill="x")
        self.input.bind("<Control-Return>", lambda _e: self.send())

        quick = ttk.LabelFrame(left, text="Quick prompts", padding=6)
        quick.pack(fill="x", pady=(8, 0))
        row = 0
        for label, prompt in QUICK_PROMPTS:
            ttk.Button(quick, text=label, width=9,
                       command=lambda p=prompt: self.prefill(p)).grid(row=row // 3, column=row % 3,
                                                                      sticky="ew", padx=2, pady=2)
            row += 1
        for column in range(3):
            quick.columnconfigure(column, weight=1)

        # ---- right column: keys + templates
        keys = ttk.LabelFrame(right, text="AI keys (required)", padding=6)
        keys.pack(fill="x")
        ttk.Label(keys, text="Provider chain", foreground="#666666").grid(row=0, column=0, sticky="w")
        self.provider_var = tk.StringVar(value="auto")
        chain = ttk.Combobox(keys, textvariable=self.provider_var, state="readonly", width=18,
                             values=("auto",) + appdata.PROVIDER_ORDER)
        chain.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(0, 6))
        chain.bind("<<ComboboxSelected>>", lambda _e: self._save_settings())

        self.key_entries = {}
        self.key_status = {}
        for index, provider_id in enumerate(appdata.PROVIDER_ORDER):
            provider = appdata.PROVIDERS[provider_id]
            ttk.Label(keys, text=provider["label"]).grid(row=2 + index * 2, column=0, sticky="w")
            entry = ttk.Entry(keys, width=22, show="*")
            entry.grid(row=2 + index * 2, column=1, columnspan=2, sticky="ew")
            entry.bind("<FocusOut>", lambda _e: self._save_settings())
            self.key_entries[provider_id] = entry
            ttk.Label(keys, text=provider["keyHelp"], foreground="#777777",
                      wraplength=210, justify="left").grid(row=3 + index * 2, column=0, columnspan=3,
                                                           sticky="w")

        ttk.Button(keys, text="Show key", command=self.reveal_keys).grid(row=8, column=0, sticky="ew")
        ttk.Button(keys, text="Hide key", command=self.hide_keys).grid(row=8, column=1, sticky="ew")
        ttk.Button(keys, text="Test chain", command=self.test_chain).grid(row=8, column=2, sticky="ew")
        keys.columnconfigure(1, weight=1)
        keys.columnconfigure(2, weight=1)

        self.template_frame = ttk.LabelFrame(right, text="templates/ — player controls", padding=6)
        self.template_frame.pack(fill="both", expand=True, pady=(8, 0))
        self.template_list = tk.Listbox(self.template_frame, height=8, font=("Consolas", 9),
                                        background="#12151c", foreground="#dfe6f2",
                                        highlightthickness=1, highlightbackground="#2a3141")
        self.template_list.pack(fill="both", expand=True)
        ttk.Label(self.template_frame,
                  text=PHASE2_NOTE, wraplength=280, justify="left",
                  foreground="#8a94a8").pack(fill="x", pady=(6, 4))
        trow = ttk.Frame(self.template_frame)
        trow.pack(fill="x")
        ttk.Button(trow, text="Copy", command=self.copy_template, width=9).pack(side="left")
        ttk.Button(trow, text="Open in Lab", command=self.open_template_in_lab,
                   width=13).pack(side="left", padx=4)
        ttk.Button(trow, text="Ask AI", command=self.ask_about_template, width=9).pack(side="left")
        self._load_templates()

    # -- settings ---------------------------------------------------------- #
    def _load_settings(self) -> None:
        settings = appdata.config()
        self.provider_var.set(settings.get("aiProvider", "auto"))
        for provider_id, provider in appdata.PROVIDERS.items():
            self.key_entries[provider_id].delete(0, "end")
            self.key_entries[provider_id].insert(0, str(settings.get(provider["keyField"], "")))

    def _save_settings(self) -> None:
        patch = {"aiProvider": self.provider_var.get()}
        for provider_id, provider in appdata.PROVIDERS.items():
            patch[provider["keyField"]] = self.key_entries[provider_id].get().strip()
        appdata.write_config(patch)

    def reveal_keys(self) -> None:
        for entry in self.key_entries.values():
            entry.configure(show="")

    def hide_keys(self) -> None:
        for entry in self.key_entries.values():
            entry.configure(show="*")

    # -- templates --------------------------------------------------------- #
    def _load_templates(self) -> None:
        self.template_list.delete(0, "end")
        for entry in appdata.list_templates():
            self.template_list.insert("end", entry["name"])

    def selected_template(self):
        selection = self.template_list.curselection()
        if not selection:
            return None
        templates = appdata.list_templates()
        index = selection[0]
        return templates[index] if 0 <= index < len(templates) else None

    def copy_template(self) -> None:
        entry = self.selected_template()
        if not entry:
            messagebox.showinfo("Templates", "Pick a template first.")
            return
        self.app.copy_to_clipboard(entry["source"])
        self._say("meta", f"Copied templates/{entry['name']}.js to the clipboard.")

    def open_template_in_lab(self) -> None:
        entry = self.selected_template()
        if not entry:
            messagebox.showinfo("Templates", "Pick a template first.")
            return
        self.insert_source(entry["source"], entry["name"])

    def ask_about_template(self) -> None:
        entry = self.selected_template()
        if not entry:
            messagebox.showinfo("Templates", "Pick a template first.")
            return
        self.prefill(f"Explain and improve this Bloxd.io template, then return the full "
                     f"corrected version in one fenced javascript block:\n\n{entry['source']}")

    def insert_into_lab(self) -> None:
        block = self.last_code()
        if not block:
            messagebox.showinfo("AI panel", "No code block in the last answer yet.")
            return
        self.insert_source(block["code"], "ai-" + time.strftime("%Y%m%d-%H%M%S"))

    def insert_source(self, source: str, name: str) -> None:
        try:
            path = self.app.insert_into_lab(source, name)
        except Exception as err:
            messagebox.showerror("Code Lab", f"Could not write the snippet:\n{err}")
            return
        self._say("meta", f"Saved as {appdata.relative_to_data(path)} and opened it in the Code Lab.")

    # -- chat -------------------------------------------------------------- #
    def prefill(self, prompt: str) -> None:
        self.input.delete("1.0", "end")
        self.input.insert("1.0", prompt)
        self.input.focus_set()

    def clear_chat(self) -> None:
        self.history.clear()
        self.chat.configure(state="normal")
        self.chat.delete("1.0", "end")
        self.chat.configure(state="disabled")
        self._say("assistant", "Chat cleared. " + PHASE2_NOTE)

    def _say(self, role: str, text: str) -> None:
        self.chat.configure(state="normal")
        self.chat.insert("end", f"\n[{role}]\n", role if role in ("you", "assistant", "error") else "meta")
        in_code = False
        for line in str(text).splitlines() or [""]:
            if line.strip().startswith("```"):
                in_code = not in_code
                continue
            self.chat.insert("end", line + "\n", "code" if in_code else role
                             if role in ("you", "assistant", "error") else "meta")
        self.chat.configure(state="disabled")
        self.chat.see("end")

    def last_code(self):
        for message in reversed(self.history):
            if message.get("role") != "assistant":
                continue
            block = extract_fenced_code(message.get("content", ""))
            if block:
                return block
        return None

    def copy_last_code(self) -> None:
        block = self.last_code()
        if not block:
            messagebox.showinfo("AI panel", "No fenced code block found in the last answer.")
            return
        self.app.copy_to_clipboard(block["code"])
        self._say("meta", "Copied the last fenced code block.")

    def save_last_code_as_snippet(self) -> None:
        from tkinter import simpledialog

        block = self.last_code()
        if not block:
            messagebox.showinfo("AI panel", "No fenced code block found in the last answer.")
            return
        name = simpledialog.askstring("Save as snippet", "Snippet name:",
                                      initialvalue="ai-" + time.strftime("%Y%m%d-%H%M%S"),
                                      parent=self)
        if not name:
            return
        try:
            path = appdata.write_snippet(name, block["code"])
        except OSError as err:
            messagebox.showerror("Save as snippet", str(err))
            return
        self.app.on_snippets_changed()
        self._say("meta", f"Saved {appdata.relative_to_data(path)} ({len(block['code'])} chars).")

    def test_chain(self) -> None:
        self._save_settings()
        settings = appdata.config()
        chain = [appdata.PROVIDERS[pid]["label"] + " ✓" for pid in resolve_chain(settings)]
        self._say("meta", "Chain: " + " → ".join(chain))
        for provider_id in appdata.PROVIDER_ORDER:
            provider = appdata.PROVIDERS[provider_id]
            key = str(settings.get(provider["keyField"]) or "").strip()
            if not key:
                self._say("meta", f"  {provider['label']}: no key (skipped)")
            else:
                self._say("meta", f"  {provider['label']}: {appdata.mask_key(key)}")
        self.prefill("Say hello in one short sentence to confirm the AI works.")

    # -- sending ----------------------------------------------------------- #
    def current_source(self) -> str:
        try:
            return self.app.get_editor_source() or ""
        except Exception:
            return ""

    def send(self) -> None:
        if self.busy:
            self._say("meta", "Still waiting on the previous answer…")
            return
        text = self.input.get("1.0", "end").strip()
        if not text:
            return
        self.input.delete("1.0", "end")
        self._save_settings()
        self._say("you", text)
        self.history.append({"role": "user", "content": text})

        source = self.current_source()
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        if source:
            messages.append({
                "role": "system",
                "content": "This is the snippet currently open in the Code Lab:\n"
                           "```javascript\n" + source + "\n```",
            })
        messages.extend(self.history)

        settings = appdata.config()
        self.busy = True
        self._say("meta", "contacting AI…")
        threading.Thread(target=self._worker, args=(messages, settings), daemon=True).start()

    def _worker(self, messages, settings) -> None:
        try:
            answer = request_completion(
                messages, settings,
                on_provider=lambda label, model: self.results.put(("provider", label, model)),
            )
            self.results.put(("done", answer))
        except AiError as err:
            self.results.put(("error", str(err)))
        except Exception as err:  # pragma: no cover - defensive
            self.results.put(("error", f"{type(err).__name__}: {err}"))

    def _poll(self) -> None:
        try:
            while True:
                kind, *rest = self.results.get_nowait()
                if kind == "provider":
                    self._say("meta", f"asking {rest[0]} (model {rest[1]})…")
                elif kind == "done":
                    answer = rest[0]
                    self.busy = False
                    self._say("assistant", answer["text"])
                    self.history.append({"role": "assistant", "content": answer["text"]})
                    self._say("meta", f"answered by {answer['provider']} · {answer['model']} · "
                                      f"{time.strftime('%H:%M:%S')}")
                elif kind == "error":
                    self.busy = False
                    self._say("error", rest[0])
        except queue.Empty:
            pass
        self.after(120, self._poll)

    # -- called by the hub ------------------------------------------------- #
    def ask_about_code(self, source: str = "") -> None:
        if source:
            self.prefill("Review this Bloxd.io snippet for engine-rule violations "
                         "(// comments, tick cost, the 500 line / 16000 char limits, "
                         "unknown api calls) and return a fixed version:\n\n" + source)
        else:
            self.prefill("What should I build first in Bloxd.io with the Code Lab?")
        self.focus_set()
        self.input.focus_set()
