import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import {
  BUNDLED_DUCKIES,
  DEFAULT_BUNDLED_DUCKY_STYLE,
} from "../../generated/bundledDuckies";
import { getApi } from "../../hooks/usePanelApi";
import type { DuckyDto } from "../../types/panel";

export interface DuckyStyle {
  id: string;
  label: string;
  kind: "builtin" | "custom";
  url: string;
}

interface DuckyCatalogContextValue {
  allStyles: DuckyStyle[];
  defaultStyle: string;
  loading: boolean;
  normalizeStyle: (styleId?: string | null) => string;
  resolveUrl: (styleId?: string | null) => string;
  labelFor: (styleId?: string | null) => string;
  refresh: () => Promise<void>;
  uploadPng: (file: File) => Promise<DuckyStyle>;
  deleteCustom: (styleId: string) => Promise<boolean>;
}

const DuckyCatalogContext = createContext<DuckyCatalogContextValue | null>(null);

function bundledToStyle(entry: (typeof BUNDLED_DUCKIES)[number]): DuckyStyle {
  return {
    id: entry.id,
    label: entry.label,
    kind: "builtin",
    url: entry.url,
  };
}

function dtoToStyle(dto: DuckyDto): DuckyStyle {
  return {
    id: dto.id,
    label: dto.label,
    kind: dto.kind,
    url: dto.url ?? (dto.kind === "custom" ? "" : `./duckies/${dto.file}`),
  };
}

function mergeCatalog(
  builtin: DuckyStyle[],
  custom: DuckyStyle[],
  defaultStyle: string,
): { allStyles: DuckyStyle[]; defaultStyle: string } {
  const byId = new Map<string, DuckyStyle>();
  for (const style of builtin) {
    byId.set(style.id, style);
  }
  for (const style of custom) {
    byId.set(style.id, style);
  }
  const allStyles = [...byId.values()].sort((a, b) => {
    if (a.kind !== b.kind) return a.kind === "builtin" ? -1 : 1;
    return a.label.localeCompare(b.label);
  });
  const fallback = allStyles[0]?.id ?? DEFAULT_BUNDLED_DUCKY_STYLE;
  const normalizedDefault = byId.has(defaultStyle) ? defaultStyle : fallback;
  return { allStyles, defaultStyle: normalizedDefault };
}

export function DuckyCatalogProvider({ children }: { children: ReactNode }) {
  const [builtinStyles] = useState(() => BUNDLED_DUCKIES.map(bundledToStyle));
  const [customStyles, setCustomStyles] = useState<DuckyStyle[]>([]);
  const [defaultStyle, setDefaultStyle] = useState(DEFAULT_BUNDLED_DUCKY_STYLE);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    const api = getApi();
    if (!api) {
      setLoading(false);
      return;
    }
    try {
      const catalog = await api.list_ducky_catalog();
      setCustomStyles((catalog.custom ?? []).map(dtoToStyle));
      setDefaultStyle(catalog.default_style || DEFAULT_BUNDLED_DUCKY_STYLE);
    } catch {
      // Keep bundled-only catalog when API is unavailable.
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const { allStyles, defaultStyle: resolvedDefault } = useMemo(
    () => mergeCatalog(builtinStyles, customStyles, defaultStyle),
    [builtinStyles, customStyles, defaultStyle],
  );

  const styleMap = useMemo(() => new Map(allStyles.map((s) => [s.id, s])), [allStyles]);

  const normalizeStyle = useCallback(
    (styleId?: string | null) => {
      const raw = (styleId || "").trim();
      if (raw && styleMap.has(raw)) return raw;
      return resolvedDefault;
    },
    [resolvedDefault, styleMap],
  );

  const resolveUrl = useCallback(
    (styleId?: string | null) => {
      const id = normalizeStyle(styleId);
      return styleMap.get(id)?.url ?? builtinStyles[0]?.url ?? "";
    },
    [builtinStyles, normalizeStyle, styleMap],
  );

  const labelFor = useCallback(
    (styleId?: string | null) => {
      const id = normalizeStyle(styleId);
      return styleMap.get(id)?.label ?? "Ducky";
    },
    [normalizeStyle, styleMap],
  );

  const uploadPng = useCallback(
    async (file: File) => {
      const api = getApi();
      if (!api) throw new Error("Panel API unavailable");

      if (file.type !== "image/png" || !file.name.toLowerCase().endsWith(".png")) {
        throw new Error("Only PNG images are supported");
      }

      const dataUrl = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result ?? ""));
        reader.onerror = () => reject(new Error("Could not read file"));
        reader.readAsDataURL(file);
      });

      const dto = await api.upload_custom_ducky(file.name, dataUrl);
      const style = dtoToStyle(dto);
      setCustomStyles((prev) => {
        const next = prev.filter((s) => s.id !== style.id);
        return [...next, style].sort((a, b) => a.label.localeCompare(b.label));
      });
      return style;
    },
    [],
  );

  const deleteCustom = useCallback(async (styleId: string) => {
    const api = getApi();
    if (!api) return false;
    const result = await api.delete_custom_ducky(styleId);
    if (result.ok) {
      setCustomStyles((prev) => prev.filter((s) => s.id !== styleId));
    }
    return result.ok;
  }, []);

  const value = useMemo(
    () => ({
      allStyles,
      defaultStyle: resolvedDefault,
      loading,
      normalizeStyle,
      resolveUrl,
      labelFor,
      refresh,
      uploadPng,
      deleteCustom,
    }),
    [
      allStyles,
      resolvedDefault,
      loading,
      normalizeStyle,
      resolveUrl,
      labelFor,
      refresh,
      uploadPng,
      deleteCustom,
    ],
  );

  return <DuckyCatalogContext.Provider value={value}>{children}</DuckyCatalogContext.Provider>;
}

export function useDuckyCatalog(): DuckyCatalogContextValue {
  const ctx = useContext(DuckyCatalogContext);
  if (!ctx) {
    throw new Error("useDuckyCatalog must be used within DuckyCatalogProvider");
  }
  return ctx;
}

export function useDuckyCatalogOptional(): DuckyCatalogContextValue | null {
  return useContext(DuckyCatalogContext);
}

export function randomDuckyStyleFromCatalog(catalog: DuckyCatalogContextValue): string {
  const styles = catalog.allStyles;
  if (!styles.length) return catalog.defaultStyle;
  const index = Math.floor(Math.random() * styles.length);
  return styles[index]?.id ?? catalog.defaultStyle;
}
