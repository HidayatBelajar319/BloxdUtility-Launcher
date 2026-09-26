/* templates/feature_toggles.js — Players Launcher
 *
 * Runtime feature flags (pvp, doubleJump, noclip, flySpeed) with a chat
 * command to flip them. PASTE INTO WORLD CODE (F8).
 *
 * This is the "which features are on right now" layer a future in-game AI
 * player reads before it acts. v1 ships the flags and the commands; nothing
 * drives them automatically yet.
 */

globalThis.feature = globalThis.feature || {};
globalThis.feature.flags = globalThis.feature.flags || {
  pvp: true,
  doubleJump: false,
  noclip: false,
  flySpeed: 8,
  owners: [],
};

globalThis.feature.isEnabled = function (name) {
  return globalThis.feature.flags[name] === true;
};

globalThis.feature.set = function (playerId, name, value) {
  if (!(name in globalThis.feature.flags)) {
    return false;
  }
  if (globalThis.feature.flags.owners.indexOf(playerId || myId) === -1) {
    api.sendMessage(playerId || myId, 'you are not allowed to change "' + name + '"');
    return false;
  }
  globalThis.feature.flags[name] = value;
  api.broadcastMessage('feature "' + name + '" is now ' + (value ? 'on' : 'off'));
  return true;
};

globalThis.feature.toggle = function (playerId, name) {
  return globalThis.feature.set(playerId, name, !globalThis.feature.isEnabled(name));
};

/* "!feature pvp off" in chat. */
globalThis.feature.chatCommand = function (playerId, message) {
  const parts = String(message || '').split(' ');
  if (parts[0] !== '!feature' || parts.length < 2) {
    return false;
  }
  const value = parts[2] === 'on' ? true : parts[2] === 'off' ? false : !globalThis.feature.isEnabled(parts[1]);
  return globalThis.feature.set(playerId, parts[1], value);
};

globalThis.feature.applyDoubleJump = function (playerId) {
  if (!globalThis.feature.isEnabled('doubleJump')) {
    return false;
  }
  api.setVelocity(playerId || myId, 0, 9, 0);
  return true;
};
