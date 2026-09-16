/**
 * One `src/manifest.json` per extension, two browsers out of it.
 *
 * The source file stays browser-neutral and Chrome-shaped (Chrome is the
 * primary target, and `tests/test_browse_install.py` parses that file as JSON
 * to check the extension id), so the Chrome build is the identity transform
 * and only Firefox needs rewriting:
 *
 * - `background.service_worker` does not exist in Firefox MV3; it wants
 *   `background.scripts`.
 * - `key` and `minimum_chrome_version` are Chrome-only keys.
 *
 * `file:///*` needs no rewrite: it is the only form Firefox accepts and Chrome
 * accepts it too, so both builds use what the source file says.
 */
export function manifestFor(target, manifest) {
  if (target === "chrome") return manifest;
  const { key, minimum_chrome_version, background, ...rest } = manifest;
  if (!background?.service_worker) return background ? { ...rest, background } : rest;
  return {
    ...rest,
    background: {
      scripts: [background.service_worker],
      ...(background.type ? { type: background.type } : {}),
    },
  };
}
