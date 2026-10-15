/* eslint-disable no-control-regex */
/**
 * Strips ANSI escape codes and resolves carriage-return (\r) overwrites
 * so terminal build logs are clean and readable.
 */
export function cleanLog(raw) {
  if (!raw) return '';

  // 1. Strip ANSI CSI escape sequences (e.g. \x1b[91m, \x1b[0m, etc.)
  let cleaned = raw.replace(/\x1b\[[0-9;?]*[ -/]*[@-~]/g, '');

  // 2. Strip ANSI OSC sequences (e.g. \x1b]...\x07)
  cleaned = cleaned.replace(/\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)/g, '');

  // 3. Strip simple two-character escape sequences
  cleaned = cleaned.replace(/\x1b[@-Z\\-_]/g, '');

  // 4. Resolve carriage return (\r) progress lines (keep the final overwrite)
  const lines = cleaned.split(/\r?\n/);
  const resolvedLines = lines.map((line) => {
    if (line.includes('\r')) {
      const parts = line.split('\r');
      for (let i = parts.length - 1; i >= 0; i--) {
        if (parts[i].trim().length > 0) {
          return parts[i];
        }
      }
      return parts[parts.length - 1];
    }
    return line;
  });

  return resolvedLines.join('\n');
}
