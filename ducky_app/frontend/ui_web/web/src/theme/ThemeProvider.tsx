import { useEffect } from "react";
import { useAppearance } from "./AppearanceContext";
import { syncAllGoogleFontLinks, syncGoogleMonoFontLink, syncGoogleUIFontLink } from "./googleFont";

export function ThemeProvider() {
  const { styleBlock, cssVars, fontLibrary } = useAppearance();

  useEffect(() => {
    syncAllGoogleFontLinks(fontLibrary.entries);
    syncGoogleUIFontLink(cssVars["font-ui"] || "");
    syncGoogleMonoFontLink(cssVars["font-mono"] || "");
  }, [cssVars, fontLibrary.entries]);

  return <style>{styleBlock}</style>;
}
