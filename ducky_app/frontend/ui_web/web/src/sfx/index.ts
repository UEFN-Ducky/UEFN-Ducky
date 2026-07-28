/** Public seams for the sound/hooks system. */
export { emitAppHook, subscribeAppHooks, APP_HOOKS, DUCKY_HOOK_EVENT } from "./appHooks";
export type { AppHookId, AppHookDef, AppHookDetail } from "./appHooks";
export { SoundFxBridge, previewSound } from "./SoundFxBridge";
export { SoundsSection } from "./SoundsSection";
export type { SoundsSettings, SoundRef } from "./soundFx";
