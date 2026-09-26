/* templates/move_player.js — Players Launcher
 *
 * Movement helper: walk, sprint, jump and stop for the current player.
 * PASTE INTO WORLD CODE (F8); call the functions from a Code Block or a
 * "press to code" board.
 *
 * This is the movement layer a future in-game AI player would drive. In v1 the
 * launcher does NOT connect it to anything live — you trigger it yourself.
 */

globalThis.feature = globalThis.feature || {};
globalThis.feature.move = globalThis.feature.move || {
  speed: 1,
  sprintMultiplier: 1.6,
  jumpPower: 9,
  facing: 0,
  state: 'idle',
  target: null,
};

/* direction: 0 = +X, 1 = +Z, 2 = -X, 3 = -Z (or pass a [dx, dz] vector). */
globalThis.feature.move.setDirection = function (playerId, direction) {
  let dx = 0;
  let dz = 0;
  if (Array.isArray(direction)) {
    dx = Number(direction[0]) || 0;
    dz = Number(direction[1]) || 0;
  } else {
    const table = [[1, 0], [0, 1], [-1, 0], [0, -1]];
    const step = table[Number(direction) % 4] || table[0];
    dx = step[0];
    dz = step[1];
  }
  const length = Math.sqrt(dx * dx + dz * dz) || 1;
  const power = globalThis.feature.move.speed * globalThis.feature.move.sprintMultiplier;
  api.setVelocity(playerId || myId, (dx / length) * power, 0, (dz / length) * power);
  globalThis.feature.move.state = 'walking';
  return true;
};

globalThis.feature.move.jump = function (playerId, power) {
  const boost = Number(power) || globalThis.feature.move.jumpPower;
  api.setVelocity(playerId || myId, 0, boost, 0);
  return boost;
};

globalThis.feature.move.stop = function (playerId) {
  api.setVelocity(playerId || myId, 0, 0, 0);
  globalThis.feature.move.state = 'idle';
  globalThis.feature.move.target = null;
  return true;
};

/* Step toward a world position one tick at a time. */
globalThis.feature.move.stepToward = function (playerId, target) {
  if (!target) {
    return globalThis.feature.move.stop(playerId);
  }
  const from = thisPos || target;
  return globalThis.feature.move.setDirection(playerId, [target[0] - from[0], target[2] - from[2]]);
};
