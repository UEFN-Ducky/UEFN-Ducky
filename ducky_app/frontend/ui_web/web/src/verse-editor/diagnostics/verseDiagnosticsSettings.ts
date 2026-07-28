let cacheEnabled = true;
let autoCheck = true;

export function syncVerseDiagnosticsSettings(settings: {
  verse_diagnostics_cache_enabled?: boolean;
  verse_diagnostics_auto_check?: boolean;
}): void {
  if (typeof settings.verse_diagnostics_cache_enabled === "boolean") {
    cacheEnabled = settings.verse_diagnostics_cache_enabled;
  }
  if (typeof settings.verse_diagnostics_auto_check === "boolean") {
    autoCheck = settings.verse_diagnostics_auto_check;
  }
}

export function isVerseDiagnosticsCacheEnabled(): boolean {
  return cacheEnabled;
}

export function isVerseDiagnosticsAutoCheckEnabled(): boolean {
  return autoCheck;
}
