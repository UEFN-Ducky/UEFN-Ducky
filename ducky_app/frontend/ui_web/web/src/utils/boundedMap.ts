/**
 * Insert into a Map with a hard cap, evicting the least recently used key.
 *
 * Several module-level caches in the panel only ever grew: collapse state per
 * tool card, composer drafts per chat, turn timers, an audio element per sound.
 * None of them was huge on its own, but nothing ever removed an entry, so a
 * long session leaked for as long as it ran. `chatMessagesCache` already solved
 * this by capping at 24; this is that same routine, written once.
 *
 * Insertion order is the recency order, so re-inserting a key moves it to the
 * end and the oldest key is the first one `keys()` yields.
 */
export function boundedSet<K, V>(map: Map<K, V>, key: K, value: V, max: number): void {
  map.delete(key);
  map.set(key, value);
  while (map.size > max) {
    const oldest = map.keys().next().value;
    if (oldest === undefined) break;
    map.delete(oldest);
  }
}

/** Read and refresh recency, so an entry in active use is not the next evicted. */
export function boundedGet<K, V>(map: Map<K, V>, key: K): V | undefined {
  const hit = map.get(key);
  if (hit === undefined) return undefined;
  map.delete(key);
  map.set(key, hit);
  return hit;
}
