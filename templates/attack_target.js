/* templates/attack_target.js — Players Launcher
 *
 * Attack helper: cooldown-gated hit attempts against a target player id.
 * PASTE INTO WORLD CODE (F8) and call it from a Code Block.
 *
 * Kept deliberately small: the tick callback fires 20x per second, so the
 * cooldown gate and the single api.* calls stay cheap. Real damage is applied
 * by the game — this helper positions, announces and delegates.
 */

globalThis.feature = globalThis.feature || {};
globalThis.feature.attack = globalThis.feature.attack || {
  range: 4,
  cooldownMs: 700,
  readyAt: 0,
  targetId: 0,
  lastHitAt: 0,
};

/* Pick the closest other player from a [x, y, z] list. */
globalThis.feature.attack.findTarget = function (origin, players) {
  let best = 0;
  let bestDistance = globalThis.feature.attack.range;
  for (const entry of players || []) {
    if (!entry || entry.id === myId || !entry.pos) {
      continue;
    }
    const dx = entry.pos[0] - origin[0];
    const dy = entry.pos[1] - origin[1];
    const dz = entry.pos[2] - origin[2];
    const distance = Math.sqrt(dx * dx + dy * dy + dz * dz);
    if (distance < bestDistance) {
      bestDistance = distance;
      best = entry.id;
    }
  }
  return best;
};

/* Returns true when the swing was allowed to happen. */
globalThis.feature.attack.tryHit = function (attackerId, targetId) {
  const now = api.now();
  if (!attackerId || !targetId || attackerId === targetId) {
    return false;
  }
  if (now < globalThis.feature.attack.readyAt) {
    return false;
  }
  globalThis.feature.attack.readyAt = now + globalThis.feature.attack.cooldownMs;
  globalThis.feature.attack.lastHitAt = now;
  globalThis.feature.attack.targetId = targetId;

  api.sendMessage(attackerId, 'hit ' + targetId);
  api.log('[attack] ' + attackerId + ' -> ' + targetId + ' at ' + now);
  return true;
};

/* Nudge the attacker toward the target. applyImpulse is the cheap version. */
globalThis.feature.attack.lunge = function (attackerId, targetPos) {
  if (!targetPos) {
    return;
  }
  const self = thisPos || targetPos;
  const dx = targetPos[0] - self[0];
  const dz = targetPos[2] - self[2];
  const length = Math.sqrt(dx * dx + dz * dz) || 1;
  api.applyImpulse(attackerId || myId, (dx / length) * 4, 6, (dz / length) * 4);
};
