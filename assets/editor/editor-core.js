'use strict';

/**
 * Bloxd Editor — editor-core.js
 *
 * The shared engine behind every Bloxd code surface (CLI vibe-coding commands
 * and the Launcher Code Lab). Zero dependencies, CommonJS, and safe to load in
 * Node (require) or in the browser (window.BloxdEditorCore).
 *
 * Contract (do not change the exported names or return shapes without bumping
 * the documented version in editor/README.md):
 *   loadApiVocabulary(fetcher) -> Promise<Vocabulary>
 *   getCompletions(linePrefix, vocab) -> Completion[]
 *   tokenizeLine(line) -> Token[]
 *   validateSource(src, vocab) -> Diagnostic[]
 *   trainReport(src, vocab) -> Report
 */

/* -------------------------------------------------------------------------- */
/* Constants                                                                    */
/* -------------------------------------------------------------------------- */

const VERSION = '1.0.0';

const REPO = 'Bloxdy/code-api';
const BRANCH = 'main';
const CONTENTS_URL = `https://api.github.com/repos/${REPO}/contents`;
const RAW_ROOT = 'https://raw.githubusercontent.com';
const DOC_EXTENSIONS = ['.md', '.txt'];

/** Bloxd Code Block script limits. */
const MAX_CHARS = 16000;
const MAX_LINES = 500;

/** Bloxd JavaScript keywords (no `//` comments, no ES modules). */
const KEYWORDS = [
  'break', 'case', 'catch', 'const', 'continue', 'default', 'delete', 'do',
  'else', 'false', 'finally', 'for', 'function', 'if', 'in', 'instanceof',
  'let', 'new', 'null', 'return', 'switch', 'this', 'throw', 'true', 'try',
  'typeof', 'undefined', 'var', 'void', 'while',
];

/** Engine globals the tokenizer highlights like keywords. */
const GLOBALS = [
  'api', 'console', 'Math', 'Date', 'JSON', 'Array', 'Object', 'String',
  'Number', 'Boolean', 'myId', 'playerId', 'thisPos', 'ownerDbId', 'localPlayer',
];

/* -------------------------------------------------------------------------- */
/* Hardcoded minimal fallback vocabulary (offline / failure path)               */
/* -------------------------------------------------------------------------- */

const FALLBACK_VOCABULARY = {
  functions: [
    { name: 'setPosition', description: 'Move an entity to a position.', example: 'api.setPosition(myId, 0, 10, 0)' },
    { name: 'getPosition', description: 'Read an entity position.', example: 'api.getPosition(myId)' },
    { name: 'getPlayerIds', description: 'All player ids currently in the world.', example: 'api.getPlayerIds()' },
    { name: 'setBlock', description: 'Place a block. Block names are case-sensitive.', example: "api.setBlock(x, y, z, 'Stone')" },
    { name: 'getBlock', description: 'Read the block name at a position.', example: 'api.getBlock(x, y, z)' },
    { name: 'setBlockRect', description: 'Fill a cuboid between two corners — much faster than per-block loops.', example: "api.setBlockRect({x:0,y:0,z:0}, {x:9,y:9,z:9}, 'Stone')" },
    { name: 'giveItem', description: 'Give an item to a player and return how many were added.', example: "api.giveItem(myId, 'Sword', 1)" },
    { name: 'sendMessage', description: 'Send a chat message to one player.', example: "api.sendMessage(myId, 'hello')" },
    { name: 'broadcastMessage', description: 'Send a chat message to every player.', example: "api.broadcastMessage('hello')" },
    { name: 'setHealth', description: 'Change an entity health value.', example: 'api.setHealth(myId, 20)' },
    { name: 'applyImpulse', description: 'Apply an impulse to an entity.', example: 'api.applyImpulse(myId, 0, 12, 0)' },
    { name: 'setVelocity', description: 'Set an entity velocity directly.', example: 'api.setVelocity(myId, 0, 8, 0)' },
    { name: 'log', description: 'Print a message to the Code Block console.', example: "api.log('hi')" },
  ],
  blocks: [
    'air', 'stone', 'dirt', 'grass', 'wood', 'sand', 'water', 'lava', 'glass',
    'brick', 'planks', 'leaves', 'obsidian', 'bedrock', 'glowstone', 'wool',
  ],
  items: [
    'sword', 'axe', 'pickaxe', 'shovel', 'helmet', 'chestplate', 'leggings',
    'boots', 'shield', 'bread', 'torch', 'bucket', 'arrow', 'bow',
  ],
  callbacks: [
    'tick', 'onPlayerJoin', 'onPlayerLeave', 'onPlayerChat',
    'onPlayerAttemptSpawnMob', 'onWorldAttemptSpawnMob', 'onWorldChangeBlock',
    'onPlayerDamage', 'onPlayerDeath', 'onPlayerMove',
  ],
};

/* -------------------------------------------------------------------------- */
/* Fetch plumbing                                                               */
/* -------------------------------------------------------------------------- */

/** Default fetcher: global fetch (Node >= 18 / browsers). */
function defaultFetcher(url) {
  if (typeof globalThis !== 'undefined' && typeof globalThis.fetch === 'function') {
    return globalThis.fetch(url, { headers: { Accept: 'application/vnd.github+json' } });
  }
  return Promise.reject(new Error('No global fetch available — pass a fetcher to loadApiVocabulary().'));
}

function asText(value) {
  if (value === null || value === undefined) return '';
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  return '';
}

function cap(text, max) {
  const clean = String(text || '').replace(/\s+/g, ' ').trim();
  if (clean.length <= max) return clean;
  return `${clean.slice(0, Math.max(0, max - 1))}…`;
}

function dedupe(list) {
  const seen = new Set();
  const out = [];
  for (const entry of list || []) {
    const key = String(entry).trim();
    if (!key) continue;
    const lower = key.toLowerCase();
    if (seen.has(lower)) continue;
    seen.add(lower);
    out.push(key);
  }
  return out;
}

function cloneVocabulary(vocab) {
  return {
    functions: (vocab.functions || []).map((entry) => ({
      name: String(entry.name || ''),
      description: String(entry.description || ''),
      example: String(entry.example || ''),
    })),
    blocks: dedupe(vocab.blocks || []),
    items: dedupe(vocab.items || []),
    callbacks: dedupe(vocab.callbacks || []),
  };
}

function fallbackVocabulary() {
  return cloneVocabulary(FALLBACK_VOCABULARY);
}

/* -------------------------------------------------------------------------- */
/* Doc text extraction helpers                                                  */
/* -------------------------------------------------------------------------- */

const FUNC_HEADING = /^#{1,6}\s*`?\s*api\.([A-Za-z_$][\w$]*)\s*\(([^)]*)\)\s*`?\s*:?\s*$/;
const FUNC_INLINE = /api\.([A-Za-z_$][\w$]*)\s*\(([^)]*)\)/;
const FUNC_ROW = /^\s*(?:[-*+]\s*)?\|?\s*`?api\.([A-Za-z_$][\w$]*)\s*\(([^)]*)\)`?\s*\|?\s*(?:[-:|]\s*)?(.*)$/;

/** Splits doc text into lines, keeping an index for diagnostics. */
function docLines(text) {
  return String(text || '').split(/\r?\n/);
}

/** Is this doc file about block names / item names / callbacks / functions? */
function classifyFile(name) {
  const upper = String(name || '').toUpperCase();
  if (upper.includes('CALLBACK') || upper.includes('EVENT')) return 'callbacks';
  if (upper.includes('ITEM')) return 'items';
  if (upper.includes('BLOCK') || upper.includes('NAME')) return 'blocks';
  return 'functions';
}

/**
 * Pulls every `api.fn(args)` documented in a doc file, with the prose and the
 * first fenced example that follows it. Tolerant of headings, table rows and
 * plain `api.fn(a, b) - description` lines.
 */
function extractFunctions(text) {
  const lines = docLines(text);
  const found = [];
  const byName = new Map();

  const record = (name, args, description, example) => {
    const key = name.toLowerCase();
    const clean = {
      name,
      description: cap(description, 240),
      example: cap(example, 240),
    };
    if (byName.has(key)) {
      const existing = byName.get(key);
      if (!existing.description && clean.description) existing.description = clean.description;
      if (!existing.example && clean.example) existing.example = clean.example;
      return;
    }
    byName.set(key, clean);
    found.push(clean);
  };

  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i];
    const heading = line.match(FUNC_HEADING);
    const row = line.match(FUNC_ROW);

    if (heading || (row && !/^\s*#/.test(line))) {
      const name = heading ? heading[1] : row[1];
      if (!name) continue;
      const inline = line.match(FUNC_INLINE);
      const firstLineDescription = heading
        ? lines[i + 1] || ''
        : (row && row[3] ? row[3] : '');

      // Prose = following non-empty lines until the next heading or entry.
      const prose = [];
      for (let j = i + 1; j < lines.length && prose.length < 3; j += 1) {
        const next = lines[j];
        if (/^\s*#{1,6}\s/.test(next)) break;
        if (/^\s*```/.test(next)) break;
        if (FUNC_ROW.test(next) && !next.startsWith('#')) break;
        if (!next.trim()) {
          if (prose.length) break;
          continue;
        }
        prose.push(next.trim());
      }

      // First fenced code block after the prose becomes the example.
      let example = '';
      for (let j = i + 1; j < Math.min(lines.length, i + 40); j += 1) {
        if (/^\s*```/.test(lines[j])) {
          example = (lines[j + 1] || '').trim();
          break;
        }
        if (/^\s*#{1,6}\s/.test(lines[j]) && j > i + 1) break;
      }

      record(
        name,
        inline ? inline[2] : (heading ? heading[2] : ''),
        [firstLineDescription, prose.join(' ')].filter(Boolean).join(' '),
        example,
      );
      continue;
    }

    // Prose mentions: "Use api.setBlock(x, y, z) to place a block."
    const mentioned = line.match(FUNC_INLINE);
    if (mentioned && !/^\s*#/.test(line)) {
      record(mentioned[1], mentioned[2], line, '');
    }
  }

  return found;
}

/**
 * Name catalogs come in three flavours: JSON arrays/objects, markdown tables
 * and one-name-per-line text files. All three are handled here.
 */
function extractNames(text) {
  const out = [];
  const trimmed = String(text || '').trim();

  if (trimmed.startsWith('{') || trimmed.startsWith('[')) {
    try {
      const parsed = JSON.parse(trimmed);
      const buckets = Array.isArray(parsed)
        ? [parsed]
        : [parsed.blocks, parsed.items, parsed.names, parsed].filter(Boolean);
      for (const bucket of buckets) {
        const values = Array.isArray(bucket) ? bucket : Object.values(bucket || {});
        for (const value of values) {
          if (typeof value === 'string') out.push(value);
          else if (value && typeof value === 'object') out.push(asText(value.name || value.blockName || value.itemName));
        }
      }
      if (out.length) return dedupe(out);
    } catch {
      /* not JSON after all — fall through to line parsing */
    }
  }

  for (const raw of docLines(trimmed)) {
    let line = raw.trim();
    if (!line || line.startsWith('#') || line.startsWith('```') || /^[-=_|]+$/.test(line)) continue;
    line = line.replace(/^[-*+]\s+/, '').replace(/^\d+[.)]\s+/, '');
    if (line.startsWith('|')) line = line.replace(/^\|/, '').replace(/\|$/, '');
    const cells = line.split('|').map((cell) => cell.trim());
    // `- \`Diamond Sword\` - melee weapon.` -> the first cell is the name.
    const first = asText(cells[0]).split(/\s+(?:[-–—:]|->)\s+/)[0];
    const clean = first.replace(/[`"']/g, '').trim();
    if (!clean || clean.length > 60) continue;
    out.push(clean);
  }

  return dedupe(out);
}

/** Callback docs are free-form prose, so identifier-ish tokens are harvested. */
function extractCallbacks(text) {
  const out = [];
  const known = new Set(FALLBACK_VOCABULARY.callbacks);
  const lines = docLines(text);

  for (const raw of lines) {
    const line = raw.trim();
    if (!line || line.startsWith('```')) continue;
    const body = line.replace(/^#{1,6}\s*/, '').replace(/^[-*+]\s*/, '').replace(/^\|/, '').replace(/\|$/, '');
    if (!body) continue;
    if (/^api\./.test(body)) continue;

    // `### onPlayerJoin / onPlayerLeave`, `onPlayerChat - fires when ...`
    const head = body.split(/\s*(?:-|–|—|:)\s+/)[0];
    for (const token of head.split(/[^A-Za-z_$][^A-Za-z0-9_$]*/)) {
      const name = token.trim();
      if (!/^[a-z][A-Za-z0-9_]{2,39}$/.test(name)) continue;
      if (KEYWORDS.includes(name) || GLOBALS.includes(name)) continue;
      if (/^(the|and|with|this|that|from|your|when|all|any|not|use|uses|used|api|player|players|world|block|blocks|entity|entities)$/i.test(name)) continue;
      if (known.has(name) || /^(on|tick|init|start|stop|update)/.test(name)) {
        out.push(name);
        known.add(name);
      }
    }
  }

  return dedupe(out);
}

/* -------------------------------------------------------------------------- */
/* 1. loadApiVocabulary                                                         */
/* -------------------------------------------------------------------------- */

function rawUrlFor(entry) {
  if (entry && typeof entry.download_url === 'string' && entry.download_url) return entry.download_url;
  const path = entry && entry.path ? entry.path : entry && entry.name;
  return `${RAW_ROOT}/${REPO}/${BRANCH}/${path}`;
}

/**
 * Fetches the Bloxd code-api file list, then every .md/.txt doc, and distills
 * them into {functions, blocks, items, callbacks}. Never rejects: any failure
 * falls back to a small hardcoded vocabulary.
 */
async function loadApiVocabulary(fetcher) {
  const get = typeof fetcher === 'function' ? fetcher : defaultFetcher;

  let listing;
  try {
    const response = await get(CONTENTS_URL);
    if (response && response.ok === false) throw new Error(`GitHub responded ${response.status}`);
    const payload = typeof response.json === 'function' ? await response.json() : response;
    const entries = Array.isArray(payload) ? payload : (payload && payload.items) || (payload && payload.files) || [];
    if (!Array.isArray(entries) || entries.length === 0) throw new Error('code-api listing was empty or unrecognised');
    listing = entries.filter((entry) => {
      if (!entry || (entry.type && entry.type !== 'file')) return false;
      const name = String(entry.name || entry.path || '');
      return DOC_EXTENSIONS.some((ext) => name.toLowerCase().endsWith(ext));
    });
    if (listing.length === 0) throw new Error('code-api listing contained no .md/.txt documentation files');
  } catch {
    return fallbackVocabulary();
  }

  const vocabulary = { functions: [], blocks: [], items: [], callbacks: [] };
  let documents = 0;

  for (const entry of listing) {
    let text;
    try {
      const response = await get(rawUrlFor(entry));
      if (response && response.ok === false) continue;
      text = typeof response.text === 'function' ? await response.text() : asText(response);
    } catch {
      continue;
    }
    if (!asText(text).trim()) continue;
    documents += 1;

    const kind = classifyFile(entry.name || entry.path);
    if (kind === 'blocks') vocabulary.blocks.push(...extractNames(text));
    else if (kind === 'items') vocabulary.items.push(...extractNames(text));
    else if (kind === 'callbacks') vocabulary.callbacks.push(...extractCallbacks(text));
    else vocabulary.functions.push(...extractFunctions(text));
  }

  if (documents === 0) return fallbackVocabulary();

  const functions = vocabulary.functions.filter((fn) => fn && fn.name);
  if (functions.length === 0) vocabulary.functions.push(...FALLBACK_VOCABULARY.functions);
  if (vocabulary.blocks.length === 0) vocabulary.blocks.push(...FALLBACK_VOCABULARY.blocks);
  if (vocabulary.items.length === 0) vocabulary.items.push(...FALLBACK_VOCABULARY.items);
  if (vocabulary.callbacks.length === 0) vocabulary.callbacks.push(...FALLBACK_VOCABULARY.callbacks);

  return {
    functions: dedupe(functions.map((fn) => fn.name)).map((name) => {
      const match = functions.find((fn) => fn.name.toLowerCase() === name.toLowerCase());
      return { name: match.name, description: match.description, example: match.example };
    }),
    blocks: dedupe(vocabulary.blocks),
    items: dedupe(vocabulary.items),
    callbacks: dedupe(vocabulary.callbacks),
  };
}

/* -------------------------------------------------------------------------- */
/* 2. getCompletions                                                           */
/* -------------------------------------------------------------------------- */

const MAX_COMPLETIONS = 60;

function completion(label, kind, detail, insertText) {
  return { label, kind, detail: cap(detail, 120), insertText: insertText || label };
}

function rankScore(needle, haystack) {
  const a = needle.toLowerCase();
  const b = haystack.toLowerCase();
  if (!a) return 1;
  if (b === a) return 0;
  if (b.startsWith(a)) return 1;
  if (b.includes(a)) return 2;
  return Infinity;
}

function pushCapped(list, entry) {
  if (list.length < MAX_COMPLETIONS) list.push(entry);
}

/**
 * Completion list for the text of the current line up to the caret.
 * Contexts: `api.<partial>`, a block/item name inside quotes, a bare
 * identifier (keywords + callbacks), and the empty prefix (keywords).
 */
function getCompletions(linePrefix, vocab) {
  const prefix = String(linePrefix === null || linePrefix === undefined ? '' : linePrefix);
  const vocabulary = cloneVocabulary(vocab && typeof vocab === 'object' ? vocab : fallbackVocabulary());
  const out = [];

  const apiMatch = prefix.match(/(?:^|[^A-Za-z0-9_$.])api\.([A-Za-z0-9_$]*)$/);
  if (apiMatch) {
    const partial = apiMatch[1];
    const scored = [];
    for (const fn of vocabulary.functions) {
      const score = rankScore(partial, fn.name);
      if (score === Infinity) continue;
      scored.push({ fn, score });
    }
    scored.sort((a, b) => (a.score - b.score) || a.fn.name.length - b.fn.name.length || a.fn.name.localeCompare(b.fn.name));
    for (const { fn } of scored) {
      pushCapped(out, completion(
        fn.name,
        'function',
        fn.description || fn.example,
        fn.name,
      ));
    }
    if (out.length === 0) {
      for (const fn of vocabulary.functions.slice(0, MAX_COMPLETIONS)) {
        pushCapped(out, completion(fn.name, 'function', fn.description || fn.example, fn.name));
      }
    }
    return out;
  }

  // Inside an unterminated quote -> block / item names.
  const quote = findOpenQuote(prefix);
  if (quote) {
    const partial = prefix.slice(quote.index + 1);
    const scored = [];
    for (const name of vocabulary.blocks) {
      const score = rankScore(partial, name);
      if (score !== Infinity) scored.push({ name, kind: 'block', score });
    }
    for (const name of vocabulary.items) {
      const score = rankScore(partial, name);
      if (score !== Infinity) scored.push({ name, kind: 'item', score });
    }
    scored.sort((a, b) => (a.score - b.score) || a.name.length - b.name.length || a.name.localeCompare(b.name));
    for (const entry of scored) {
      pushCapped(out, completion(entry.name, entry.kind, `${entry.kind} name`, entry.name));
    }
    return out;
  }

  const identMatch = prefix.match(/[A-Za-z_$][\w$]*$/);
  const partial = identMatch ? identMatch[0] : '';
  const addAll = partial === '';
  const scored = [];
  const consider = (name, kind, detail) => {
    const score = addAll ? 1 : rankScore(partial, name);
    if (score === Infinity) return;
    scored.push({ name, kind, detail, score });
  };

  for (const kw of KEYWORDS) consider(kw, 'keyword', 'Bloxd JavaScript keyword');
  for (const name of vocabulary.callbacks) consider(name, 'callback', 'Bloxd callback');
  if (partial.length > 1) {
    for (const fn of vocabulary.functions) consider(fn.name, 'function', fn.description || 'api function');
  }

  scored.sort((a, b) => (a.score - b.score) || a.name.length - b.name.length || a.name.localeCompare(b.name));
  for (const entry of scored) {
    pushCapped(out, completion(entry.name, entry.kind, entry.detail, entry.name));
  }
  return out;
}

/** Index + quote char of an unterminated string opener in a line prefix. */
function findOpenQuote(prefix) {
  let quoteChar = '';
  let quoteIndex = -1;
  for (let i = 0; i < prefix.length; i += 1) {
    const ch = prefix[i];
    if (ch === '\\') { i += 1; continue; }
    if (quoteChar) {
      if (ch === quoteChar) { quoteChar = ''; quoteIndex = -1; }
      continue;
    }
    if (ch === '"' || ch === "'" || ch === '`') { quoteChar = ch; quoteIndex = i; }
  }
  return quoteChar ? { index: quoteIndex, char: quoteChar } : null;
}

/* -------------------------------------------------------------------------- */
/* 3. tokenizeLine                                                              */
/* -------------------------------------------------------------------------- */

const TOKEN_TYPES = ['keyword', 'string', 'comment', 'api', 'number', 'plain'];
const KEYWORD_SET = new Set(KEYWORDS.concat(GLOBALS));
const CALLBACK_TOKEN = /^[A-Za-z_$][\w$]*$/;

/**
 * Splits one line into highlighted tokens. Bloxd has no `//` comments, but the
 * tokenizer still recognises them so validateSource() can report them.
 * Concatenating every token's `text` always reproduces the input line exactly.
 */
function tokenizeLine(line) {
  const source = line === null || line === undefined ? '' : String(line);
  const tokens = [];
  const push = (text, type) => {
    if (!text) return;
    const last = tokens[tokens.length - 1];
    if (last && last.type === type && type === 'plain') last.text += text;
    else tokens.push({ text, type });
  };

  let i = 0;
  while (i < source.length) {
    const ch = source[i];

    if (ch === '/' && source[i + 1] === '/') {
      push(source.slice(i), 'comment');
      break;
    }
    if (ch === '/' && source[i + 1] === '*') {
      const end = source.indexOf('*/', i + 2);
      if (end === -1) { push(source.slice(i), 'comment'); break; }
      push(source.slice(i, end + 2), 'comment');
      i = end + 2;
      continue;
    }
    if (ch === '"' || ch === "'" || ch === '`') {
      let j = i + 1;
      while (j < source.length) {
        if (source[j] === '\\') { j += 2; continue; }
        if (source[j] === ch) { j += 1; break; }
        j += 1;
      }
      push(source.slice(i, Math.min(j, source.length)), 'string');
      i = Math.min(j, source.length);
      continue;
    }
    if (/[0-9]/.test(ch) || (ch === '.' && /[0-9]/.test(source[i + 1] || ''))) {
      const match = source.slice(i).match(/^\d*\.?\d+(?:[eE][+-]?\d+)?/);
      if (match) { push(match[0], 'number'); i += match[0].length; continue; }
    }
    if (/[A-Za-z_$]/.test(ch)) {
      const match = source.slice(i).match(/^[A-Za-z_$][\w$]*/);
      const word = match[0];
      // A member access (`api.x`, `console.log`) is API-ish: checked first so a
      // global like `api` is not swallowed by the keyword branch.
      if (source[i + word.length] === '.') push(word, 'api');
      else if (KEYWORD_SET.has(word)) push(word, 'keyword');
      else push(word, 'plain');
      i += word.length;
      continue;
    }
    push(ch, 'plain');
    i += 1;
  }

  return tokens.filter((token) => token.text && TOKEN_TYPES.includes(token.type));
}

/** True when an identifier is a known callback name (used by the UI to style it). */
function isCallbackToken(token, vocab) {
  if (!token || !CALLBACK_TOKEN.test(token.text)) return false;
  const callbacks = (vocab && vocab.callbacks) || FALLBACK_VOCABULARY.callbacks;
  return callbacks.some((name) => String(name).toLowerCase() === token.text.toLowerCase());
}

/* -------------------------------------------------------------------------- */
/* 4. validateSource                                                            */
/* -------------------------------------------------------------------------- */

/** 1-based (line, col) of the first `//` that is not inside a string. */
function findLineComment(line) {
  let quoteChar = '';
  for (let i = 0; i < line.length; i += 1) {
    const ch = line[i];
    if (ch === '\\') { i += 1; continue; }
    if (quoteChar) {
      if (ch === quoteChar) quoteChar = '';
      continue;
    }
    if (ch === '"' || ch === "'" || ch === '`') { quoteChar = ch; continue; }
    if (ch === '/' && line[i + 1] === '/') return i;
  }
  return -1;
}

/** Every `api.fn(` name in a line, ignoring strings and `//` comments. */
function findApiCalls(line) {
  const calls = [];
  let quoteChar = '';
  let i = 0;
  while (i < line.length) {
    const ch = line[i];
    if (ch === '\\') { i += 2; continue; }
    if (quoteChar) {
      if (ch === quoteChar) quoteChar = '';
      i += 1;
      continue;
    }
    if (ch === '"' || ch === "'" || ch === '`') { quoteChar = ch; i += 1; continue; }
    if (ch === '/' && line[i + 1] === '/') break;
    if (line.startsWith('api.', i) && !/[A-Za-z0-9_$]/.test(line[i - 1] || '')) {
      const match = line.slice(i).match(/^api\.([A-Za-z_$][\w$]*)\s*\(/);
      if (match) {
        calls.push({ name: match[1], col: i + 1 });
        i += match[0].length;
        continue;
      }
    }
    i += 1;
  }
  return calls;
}

/**
 * Validates a whole script:
 *   error   — `//` comments (Bloxd forbids them; use `/* ... *\/`)
 *   warning — unknown `api.*` calls (not in the fetched vocabulary)
 *   error   — more than MAX_CHARS characters or MAX_LINES lines
 */
function validateSource(src, vocab) {
  const source = src === null || src === undefined ? '' : String(src);
  const diagnostics = [];
  const vocabulary = cloneVocabulary(vocab && typeof vocab === 'object' ? vocab : fallbackVocabulary());
  const known = new Set(vocabulary.functions.map((fn) => fn.name.toLowerCase()));
  const lines = source.split(/\r?\n/);

  if (source.length > MAX_CHARS) {
    diagnostics.push({
      line: 1,
      col: 1,
      message: `Script is ${source.length} characters — the Bloxd Code Block limit is ${MAX_CHARS}.`,
      severity: 'error',
    });
  }
  if (lines.length > MAX_LINES) {
    diagnostics.push({
      line: MAX_LINES + 1,
      col: 1,
      message: `Script is ${lines.length} lines — the Bloxd Code Block limit is ${MAX_LINES}.`,
      severity: 'error',
    });
  }

  lines.forEach((text, index) => {
    const lineNumber = index + 1;
    const commentCol = findLineComment(text);
    if (commentCol !== -1) {
      diagnostics.push({
        line: lineNumber,
        col: commentCol + 1,
        message: '`//` comments are not supported in Bloxd code — use /* block comments */ instead.',
        severity: 'error',
      });
    }
    for (const call of findApiCalls(text)) {
      if (known.has(call.name.toLowerCase())) continue;
      diagnostics.push({
        line: lineNumber,
        col: call.col,
        message: `Unknown api.${call.name}() — not found in the Bloxd code API vocabulary.`,
        severity: 'warning',
      });
    }
  });

  diagnostics.sort((a, b) => (a.line - b.line) || (a.col - b.col));
  return diagnostics;
}

/* -------------------------------------------------------------------------- */
/* 5. trainReport                                                               */
/* -------------------------------------------------------------------------- */

/**
 * The real-time training hook: cheap, synchronous stats for every keystroke.
 * `ok` is false when any *error* diagnostic exists (warnings do not block).
 */
function trainReport(src, vocab) {
  const source = src === null || src === undefined ? '' : String(src);
  const lines = source.split(/\r?\n/);
  const diagnostics = validateSource(source, vocab);
  const errors = diagnostics.filter((diagnostic) => diagnostic.severity === 'error');

  const apiCalls = [];
  const seen = new Set();
  for (const line of lines) {
    for (const call of findApiCalls(line)) {
      if (seen.has(call.name)) continue;
      seen.add(call.name);
      apiCalls.push(call.name);
    }
  }

  return {
    ok: errors.length === 0,
    errors,
    stats: {
      chars: source.length,
      lines: lines.length,
      apiCalls,
    },
  };
}

/* -------------------------------------------------------------------------- */
/* Exports                                                                      */
/* -------------------------------------------------------------------------- */

const BloxdEditorCore = {
  loadApiVocabulary,
  getCompletions,
  tokenizeLine,
  validateSource,
  trainReport,
  /* constants (additive, documented in editor/README.md) */
  VERSION,
  REPO,
  BRANCH,
  CONTENTS_URL,
  RAW_ROOT,
  MAX_CHARS,
  MAX_LINES,
  KEYWORDS,
  GLOBALS,
  TOKEN_TYPES,
  isCallbackToken,
  fallbackVocabulary,
};

if (typeof module !== 'undefined' && module.exports) {
  module.exports = BloxdEditorCore;
}
if (typeof window !== 'undefined' && window) {
  window.BloxdEditorCore = BloxdEditorCore;
}
