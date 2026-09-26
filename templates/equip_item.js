/* templates/equip_item.js — Players Launcher
 *
 * Equip / hot-swap helper. PASTE INTO WORLD CODE (F8) or run from a Code Block.
 *
 * Bloxd rules honoured here: no // comments, everything lives on globalThis so
 * Code Blocks can reach it, and only api.* calls documented in the Bloxd API
 * vocabulary are used. The Code Lab problems panel flags any call it does not
 * recognise — if it warns, check the function name in the vocabulary first.
 */

globalThis.feature = globalThis.feature || {};
globalThis.feature.equip = globalThis.feature.equip || {
  slots: { primary: 0, secondary: 1 },
  items: { primary: 'diamond_sword', secondary: 'shield' },
  cooldownUntil: 0,
};

/* Give a player an item and report what actually landed in the inventory. */
globalThis.feature.equip.give = function (playerId, itemName, count) {
  if (!playerId || !itemName) {
    api.log('[equip] give() needs a playerId and an itemName');
    return 0;
  }
  const added = api.giveItem(playerId, itemName, count || 1);
  if (added > 0) {
    api.sendMessage(playerId, 'equipped ' + itemName + ' x' + added);
  } else {
    api.sendMessage(playerId, 'inventory full — ' + itemName + ' was not added');
  }
  return added;
};

/* Hot-swap: hand the primary item, take the secondary back. */
globalThis.feature.equip.swap = function (playerId) {
  const now = api.now();
  if (now < globalThis.feature.equip.cooldownUntil) {
    return false;
  }
  globalThis.feature.equip.cooldownUntil = now + 400;

  const kit = globalThis.feature.equip.items;
  globalThis.feature.equip.give(playerId, kit.primary, 1);
  globalThis.feature.equip.give(playerId, kit.secondary, 1);
  return true;
};

/* Called from a Code Block: "press to code" board next to an item pedestal. */
globalThis.feature.equip.onPedestal = function (playerId) {
  return globalThis.feature.equip.swap(playerId || myId);
};
