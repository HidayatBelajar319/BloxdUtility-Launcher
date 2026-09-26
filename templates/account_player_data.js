/* templates/account_player_data.js — Players Launcher
 *
 * Per-player account state: score, coins, progress and settings, saved on
 * close and loaded on join. PASTE INTO WORLD CODE (F8).
 *
 * Why this file exists: "let and const are invisible to Code Blocks", so all
 * shared state has to live on globalThis or the Code Block cannot touch it.
 * This is the account layer a future in-game AI player would read and write.
 */

globalThis.accounts = globalThis.accounts || {};

globalThis.accounts.defaults = {
  coins: 0,
  score: 0,
  progress: 0,
  inventory: [],
  settings: { pvp: true, doubleJump: false, noclip: false },
  firstSeen: 0,
  lastSeen: 0,
};

globalThis.accounts.get = function (playerId) {
  const id = playerId || myId;
  if (!globalThis.accounts.byId || typeof globalThis.accounts.byId !== 'object') {
    globalThis.accounts.byId = {};
  }
  if (!globalThis.accounts.byId[id]) {
    const fresh = JSON.parse(JSON.stringify(globalThis.accounts.defaults));
    fresh.firstSeen = api.now();
    globalThis.accounts.byId[id] = fresh;
  }
  return globalThis.accounts.byId[id];
};

globalThis.accounts.addCoins = function (playerId, amount) {
  const account = globalThis.accounts.get(playerId);
  account.coins = Math.max(0, account.coins + (Number(amount) || 0));
  api.sendMessage(playerId || myId, 'coins: ' + account.coins);
  return account.coins;
};

globalThis.accounts.setProgress = function (playerId, value) {
  const account = globalThis.accounts.get(playerId);
  account.progress = Math.max(0, Number(value) || 0);
  return account.progress;
};

globalThis.accounts.onJoin = function (playerId) {
  const account = globalThis.accounts.get(playerId);
  account.lastSeen = api.now();
  api.sendMessage(playerId, 'welcome back — ' + account.coins + ' coins, stage ' + account.progress);
  return account;
};

globalThis.accounts.save = function () {
  const snapshot = JSON.stringify(globalThis.accounts.byId || {});
  globalThis.accounts.snapshot = snapshot;
  api.log('[accounts] saved ' + snapshot.length + ' bytes');
  return snapshot;
};

globalThis.accounts.onClose = function () {
  globalThis.accounts.save();
};
