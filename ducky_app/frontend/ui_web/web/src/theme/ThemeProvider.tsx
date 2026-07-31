import { useEffect } from "react";
import { useAppearance } from "./AppearanceContext";
import { syncAllGoogleFontLinks, syncGoogleMonoFontLink, syncGoogleUIFontLink } from "./googleFont";
import { setPluginThemeVars } from "../plugin-ui/pluginTheme";

export function ThemeProvider() {
  const { styleBlock, cssVars, fontLibrary } = useAppearance();

  useEffect(() => {
    syncAllGoogleFontLinks(fontLibrary.entries);
    syncGoogleUIFontLink(cssVars["font-ui"] || "");
    syncGoogleMonoFontLink(cssVars["font-mono"] || "");
  }, [cssVars, fontLibrary.entries]);

  useEffect(() => {
    setPluginThemeVars(cssVars);
  }, [cssVars]);

  return <style>{styleBlock}</style>;
}
