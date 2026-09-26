'use strict';

/**
 * Bloxd Editor — bloxd-editor.js
 *
 * Browser UI for editor-core.js. No Monaco, no bundler, no dependencies:
 * a textarea layered over a highlighted <pre> with a keyboard-navigable
 * completion popup, line numbers, a live problems panel and a debounced
 * onTrain hook.
 *
 * Usage:
 *   const editor = createBloxdEditor(document.getElementById('host'), {
 *     core: window.BloxdEditorCore,   // required (or the module itself)
 *     vocab,                           // optional, defaults to core.fallbackVocabulary()
 *     onTrain(report) { ... },         // optional
 *     value: 'api.log("hi")',          // optional initial source
 *     placeholder: 'Write Bloxd code…', // optional
 *     debounce: 200,                   // optional ms
 *   });
 */

/* -------------------------------------------------------------------------- */
/* Helpers                                                                      */
/* -------------------------------------------------------------------------- */

const DEFAULTS = {
  debounce: 200,
  placeholder: 'Write Bloxd code…',
  emptyMessage: 'Start typing — try api.setBlock(...)',
  popupLimit: 60,
};

function resolveCore(options) {
  if (options && options.core && typeof options.core.getCompletions === 'function') return options.core;
  if (typeof window !== 'undefined' && window.BloxdEditorCore) return window.BloxdEditorCore;
  throw new Error('createBloxdEditor: no core supplied (pass { core }) and window.BloxdEditorCore is missing.');
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

function escapeHtml(value) {
  return String(value === null || value === undefined ? '' : value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

/* -------------------------------------------------------------------------- */
/* Public entry point                                                           */
/* -------------------------------------------------------------------------- */

function createBloxdEditor(container, options) {
  if (!container || typeof container.appendChild !== 'function') {
    throw new Error('createBloxdEditor: a container element is required.');
  }
  const opts = Object.assign({}, DEFAULTS, options || {});
  const core = resolveCore(opts);
  let vocab = opts.vocab && typeof opts.vocab === 'object'
    ? opts.vocab
    : (typeof core.fallbackVocabulary === 'function' ? core.fallbackVocabulary() : { functions: [], blocks: [], items: [], callbacks: [] });

  const onTrain = typeof opts.onTrain === 'function' ? opts.onTrain : null;
  let trainTimer = null;
  let lastReport = null;

  /* ---------------------------------------------------------------- DOM ---- */

  const root = el('div', 'bloxd-editor');
  root.setAttribute('data-editor', 'bloxd');

  const toolbar = el('div', 'bloxd-editor__toolbar');
  const title = el('span', 'bloxd-editor__title', 'Bloxd Editor');
  const counts = el('span', 'bloxd-editor__counts', '');
  toolbar.appendChild(title);
  toolbar.appendChild(counts);

  const body = el('div', 'bloxd-editor__body');
  const gutter = el('div', 'bloxd-editor__gutter');
  gutter.setAttribute('aria-hidden', 'true');

  const codeWrap = el('div', 'bloxd-editor__code');
  const highlight = el('pre', 'bloxd-editor__highlight');
  highlight.setAttribute('aria-hidden', 'true');
  const textarea = el('textarea', 'bloxd-editor__input');
  textarea.setAttribute('spellcheck', 'false');
  textarea.setAttribute('autocomplete', 'off');
  textarea.setAttribute('autocapitalize', 'off');
  textarea.setAttribute('wrap', 'off');
  textarea.setAttribute('aria-label', 'Bloxd code editor');
  if (opts.placeholder) textarea.setAttribute('placeholder', opts.placeholder);
  codeWrap.appendChild(highlight);
  codeWrap.appendChild(textarea);

  const mirror = el('span', 'bloxd-editor__mirror');
  highlight.appendChild(mirror);

  body.appendChild(gutter);
  body.appendChild(codeWrap);

  const problems = el('div', 'bloxd-editor__problems');
  problems.setAttribute('role', 'status');

  const popup = el('div', 'bloxd-editor__popup');
  popup.setAttribute('role', 'listbox');
  popup.hidden = true;
  const popupList = el('ul', 'bloxd-editor__popup-list');
  popup.appendChild(popupList);

  root.appendChild(toolbar);
  root.appendChild(body);
  root.appendChild(problems);
  root.appendChild(popup);
  container.appendChild(root);

  /* ------------------------------------------------------------- render ---- */

  let completions = [];
  let activeIndex = 0;
  let prefixStart = 0;
  const lineMarks = new Map();

  function renderHighlight() {
    const lines = textarea.value.split('\n');
    const html = lines.map((line) => {
      const tokens = core.tokenizeLine(line);
      const rendered = tokens
        .map((token) => `<span class="bloxd-editor__tok bloxd-editor__tok--${escapeHtml(token.type)}">${escapeHtml(token.text)}</span>`)
        .join('');
      return rendered || '<span class="bloxd-editor__tok bloxd-editor__tok--plain"> </span>';
    });
    highlight.insertAdjacentHTML('afterbegin', html.join('\n'));
    // The mirror span must stay the last child of <pre>.
    highlight.appendChild(mirror);

    const numbers = lines.map((_, index) => {
      const marks = lineMarks.get(index + 1);
      const className = `bloxd-editor__line-number${marks ? ` bloxd-editor__line-number--${marks}` : ''}`;
      return `<div class="${className}">${index + 1}</div>`;
    });
    gutter.innerHTML = numbers.join('');
    syncScroll();
  }

  function markLines(diagnostics) {
    lineMarks.clear();
    let worst = null;
    for (const diagnostic of diagnostics) {
      const current = lineMarks.get(diagnostic.line);
      if (diagnostic.severity === 'error' || !current) {
        lineMarks.set(diagnostic.line, diagnostic.severity);
      }
      if (diagnostic.severity === 'error') worst = 'error';
      else if (worst !== 'error') worst = 'warning';
    }
    return worst;
  }

  function renderProblems(diagnostics) {
    const items = diagnostics.slice(0, 40);
    if (items.length === 0) {
      problems.hidden = true;
      problems.innerHTML = '';
      return;
    }
    problems.hidden = false;
    problems.innerHTML = '';
    const errors = diagnostics.filter((d) => d.severity === 'error').length;
    const warnings = diagnostics.length - errors;
    const head = el('div', 'bloxd-editor__problems-head',
      `${errors} error${errors === 1 ? '' : 's'}${warnings ? `, ${warnings} warning${warnings === 1 ? '' : 's'}` : ''}`);
    problems.appendChild(head);
    for (const diagnostic of items) {
      const row = el('button', `bloxd-editor__problem bloxd-editor__problem--${diagnostic.severity}`);
      row.type = 'button';
      row.textContent = `${diagnostic.line}:${diagnostic.col}  ${diagnostic.message}`;
      row.addEventListener('click', () => {
        focusLine(diagnostic.line, diagnostic.col);
      });
      problems.appendChild(row);
    }
    if (diagnostics.length > items.length) {
      problems.appendChild(el('div', 'bloxd-editor__problem-more', `+${diagnostics.length - items.length} more`));
    }
  }

  function renderStats(worst) {
    const lines = textarea.value.length === 0 ? 0 : textarea.value.split('\n').length;
    const chars = textarea.value.length;
    const overLines = lines > core.MAX_LINES;
    const overChars = chars > core.MAX_CHARS;
    counts.textContent = '';
    counts.appendChild(el('span', `bloxd-editor__stat${overLines ? ' bloxd-editor__stat--error' : ''}`, `${lines}/${core.MAX_LINES} lines`));
    counts.appendChild(el('span', `bloxd-editor__stat${overChars ? ' bloxd-editor__stat--error' : ''}`, `${chars}/${core.MAX_CHARS} chars`));
    if (worst === 'error') counts.appendChild(el('span', 'bloxd-editor__stat bloxd-editor__stat--error', 'error'));
    else if (worst === 'warning') counts.appendChild(el('span', 'bloxd-editor__stat bloxd-editor__stat--warning', 'warning'));
    else counts.appendChild(el('span', 'bloxd-editor__stat bloxd-editor__stat--ok', 'ready'));
  }

  function syncScroll() {
    highlight.scrollTop = textarea.scrollTop;
    highlight.scrollLeft = textarea.scrollLeft;
    gutter.scrollTop = textarea.scrollTop;
  }

  function paint() {
    const diagnostics = core.validateSource(textarea.value, vocab);
    const worst = markLines(diagnostics);
    renderHighlight();
    renderStats(worst);
    renderProblems(diagnostics);
  }

  /* ------------------------------------------------------------- popup ----- */

  function closePopup() {
    popup.hidden = true;
    completions = [];
    activeIndex = 0;
  }

  function setActive(index) {
    if (completions.length === 0) return;
    activeIndex = (index + completions.length) % completions.length;
    const nodes = popupList.children;
    for (let i = 0; i < nodes.length; i += 1) {
      nodes[i].classList.toggle('is-active', i === activeIndex);
      if (i === activeIndex && nodes[i].scrollIntoView) nodes[i].scrollIntoView({ block: 'nearest' });
    }
  }

  function caretPrefix() {
    const upto = textarea.value.slice(0, textarea.selectionStart);
    const lineStart = upto.lastIndexOf('\n') + 1;
    return {
      prefix: upto.slice(lineStart),
      lineStart,
      index: textarea.selectionStart,
      line: upto.slice(0, lineStart).split('\n').length,
    };
  }

  function positionPopup(line) {
    // Measure the caret with a hidden mirror span inside the <pre> layer.
    const lines = textarea.value.split('\n');
    const upTo = lines.slice(0, line - 1).join('\n');
    if (upTo) mirror.textContent = `${upTo}\n`;
    else mirror.textContent = '';
    const box = codeWrap.getBoundingClientRect();
    const caret = mirror.getBoundingClientRect ? mirror.getBoundingClientRect() : null;
    const padTop = parseFloat(getComputedStyle(textarea).paddingTop || '0') || 0;
    const top = (caret ? caret.top - box.top : line * 20) + padTop + 18;
    popup.style.top = `${Math.max(0, top)}px`;
    popup.style.left = `${Math.max(0, caret ? caret.left - box.left : 0)}px`;
  }

  function openPopup() {
    const caret = caretPrefix();
    const trigger = /(?:api\.[A-Za-z0-9_$]*|[A-Za-z_$][\w$]*|['"][^'"]*)$/.test(caret.prefix);
    if (!trigger) {
      closePopup();
      return;
    }
    const list = core.getCompletions(caret.prefix, vocab) || [];
    if (list.length === 0) { closePopup(); return; }

    completions = list.slice(0, opts.popupLimit);
    activeIndex = 0;
    popupList.innerHTML = '';
    for (let i = 0; i < completions.length; i += 1) {
      const item = completions[i];
      const li = el('li', 'bloxd-editor__popup-item');
      li.setAttribute('role', 'option');
      li.id = `bloxd-editor-opt-${i}`;
      li.appendChild(el('span', `bloxd-editor__popup-kind bloxd-editor__popup-kind--${item.kind}`, item.kind));
      li.appendChild(el('span', 'bloxd-editor__popup-label', item.label));
      if (item.detail) li.appendChild(el('span', 'bloxd-editor__popup-detail', item.detail));
      li.addEventListener('mousedown', (event) => {
        event.preventDefault();
        activeIndex = i;
        acceptCompletion();
      });
      popupList.appendChild(li);
    }
    popupList.setAttribute('aria-activedescendant', `bloxd-editor-opt-0`);
    popup.hidden = false;
    positionPopup(caret.line);
    setActive(0);
  }

  function acceptCompletion() {
    const choice = completions[activeIndex];
    if (!choice) { closePopup(); return; }
    const caret = caretPrefix();
    const before = textarea.value.slice(caret.lineStart, caret.index);
    // `prefixStart` can only ever be the typed token, but clamp so a synthetic
    // caret position can never splice the buffer backwards.
    const start = Math.min(caret.lineStart + prefixStart, caret.index);
    const insert = choice.insertText || choice.label;
    const after = textarea.value.slice(caret.index);
    textarea.value = textarea.value.slice(0, start) + insert + after;
    const nextCaret = start + insert.length;
    textarea.setSelectionRange(nextCaret, nextCaret);
    closePopup();
    paint();
    scheduleTrain();
    textarea.focus();
  }

  function computePrefixStart(prefix) {
    // `api.gi` -> keep the `api.` the user typed, replace only `gi`.
    const api = prefix.match(/api\.([A-Za-z0-9_$]*)$/);
    if (api) return api[0].length - api[1].length;
    // Bare identifier, including the name inside an open quote: `'Dia` -> `Dia`.
    const ident = prefix.match(/[A-Za-z_$][\w$]*$/);
    if (ident) return prefix.length - ident[0].length;
    return 0;
  }

  /* ------------------------------------------------------------- caret ----- */

  function focusLine(line, col) {
    const lines = textarea.value.split('\n');
    const target = Math.min(Math.max(1, line), lines.length);
    let offset = 0;
    for (let i = 0; i < target - 1; i += 1) offset += lines[i].length + 1;
    offset += Math.max(0, (col || 1) - 1);
    textarea.focus();
    textarea.setSelectionRange(offset, offset);
    const lineHeight = parseFloat(getComputedStyle(textarea).lineHeight || '20') || 20;
    textarea.scrollTop = Math.max(0, (target - 1) * lineHeight - 40);
    syncScroll();
  }

  /* ------------------------------------------------------------- events ---- */

  function scheduleTrain() {
    if (!onTrain) return;
    if (trainTimer) clearTimeout(trainTimer);
    trainTimer = setTimeout(() => {
      trainTimer = null;
      lastReport = core.trainReport(textarea.value, vocab);
      try {
        onTrain(lastReport);
      } catch (err) {
        /* a consumer error must never break the editor */
      }
    }, Math.max(0, opts.debounce));
  }

  textarea.addEventListener('input', () => {
    paint();
    prefixStart = computePrefixStart(caretPrefix().prefix);
    if (caretPrefix().prefix.length === 0) closePopup();
    else openPopup();
    scheduleTrain();
  });

  textarea.addEventListener('keydown', (event) => {
    if (!popup.hidden && completions.length) {
      if (event.key === 'ArrowDown') { event.preventDefault(); setActive(activeIndex + 1); return; }
      if (event.key === 'ArrowUp') { event.preventDefault(); setActive(activeIndex - 1); return; }
      if (event.key === 'Enter' || event.key === 'Tab') { event.preventDefault(); acceptCompletion(); return; }
      if (event.key === 'Escape') { event.preventDefault(); closePopup(); return; }
      if (event.key === 'PageDown') { event.preventDefault(); setActive(activeIndex + 8); return; }
      if (event.key === 'PageUp') { event.preventDefault(); setActive(activeIndex - 8); return; }
    }
    if (event.key === 'Tab' && !event.ctrlKey && !event.metaKey && !event.altKey) {
      event.preventDefault();
      insertText('\t');
      return;
    }
    if (event.key === 'Escape') closePopup();
  });

  textarea.addEventListener('click', () => closePopup());
  textarea.addEventListener('blur', () => setTimeout(closePopup, 120));
  textarea.addEventListener('scroll', syncScroll);
  textarea.addEventListener('keyup', (event) => {
    if (event.key === 'ArrowLeft' || event.key === 'ArrowRight' || event.key === 'Home' || event.key === 'End') {
      prefixStart = computePrefixStart(caretPrefix().prefix);
    }
  });

  root.addEventListener('mousedown', (event) => {
    if (popup.contains(event.target)) event.preventDefault();
  });

  function insertText(text) {
    const start = textarea.selectionStart;
    const end = textarea.selectionEnd;
    textarea.value = textarea.value.slice(0, start) + text + textarea.value.slice(end);
    const caret = start + text.length;
    textarea.setSelectionRange(caret, caret);
    paint();
    scheduleTrain();
  }

  /* ------------------------------------------------------------- public ---- */

  const api = {
    element: root,
    textarea,
    getValue: () => textarea.value,
    setValue(next) {
      textarea.value = next === null || next === undefined ? '' : String(next);
      closePopup();
      paint();
      scheduleTrain();
      return api;
    },
    getReport() {
      lastReport = core.trainReport(textarea.value, vocab);
      return lastReport;
    },
    setVocabulary(next) {
      vocab = next && typeof next === 'object' ? next : vocab;
      paint();
      return api;
    },
    getVocabulary: () => vocab,
    focus: () => textarea.focus(),
    focusLine,
    destroy() {
      if (trainTimer) clearTimeout(trainTimer);
      trainTimer = null;
      const parent = root.parentNode;
      if (parent && typeof parent.removeChild === 'function') parent.removeChild(root);
    },
  };

  textarea.value = opts.value ? String(opts.value) : '';
  paint();
  return api;
}

/* -------------------------------------------------------------------------- */
/* Exports                                                                      */
/* -------------------------------------------------------------------------- */

if (typeof module !== 'undefined' && module.exports) {
  module.exports = { createBloxdEditor, DEFAULTS };
}
if (typeof window !== 'undefined' && window) {
  window.BloxdEditor = { createBloxdEditor };
}
