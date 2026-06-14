export interface ParsedStopNotes {
  propertyType: string | null;
  safePlace: string | null;
  physicalKeyAccess: string | null;
  freeText: string;
}

const KNOWN_KEYS: Record<string, keyof Omit<ParsedStopNotes, 'freeText'>> = {
  propertytype: 'propertyType',
  safeplace: 'safePlace',
  physicalkeyaccess: 'physicalKeyAccess',
};

export function parseStopNotes(notes?: string | null): ParsedStopNotes {
  const result: ParsedStopNotes = {
    propertyType: null,
    safePlace: null,
    physicalKeyAccess: null,
    freeText: '',
  };

  if (!notes) return result;

  // Support both newline-separated and ", "-separated key:value pairs.
  const lines = notes.includes('\n') ? notes.split('\n') : notes.split(/, /);
  const freeLines: string[] = [];

  for (const raw of lines) {
    const line = raw.trim();
    if (!line) continue;
    const colonIdx = line.indexOf(':');
    if (colonIdx > 0) {
      const rawKey = line.slice(0, colonIdx).trim().toLowerCase().replace(/\s+/g, '');
      const val = line.slice(colonIdx + 1).trim();
      const mapped = KNOWN_KEYS[rawKey];
      if (mapped) {
        result[mapped] = val || null;
        continue;
      }
    }
    freeLines.push(line);
  }

  result.freeText = freeLines.join('\n');
  return result;
}
