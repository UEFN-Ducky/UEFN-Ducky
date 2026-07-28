/** Voice system public surface — prefer importing specific modules. */

export { VoiceControls, SpeakMessageButton } from "./VoiceControls";
export { VoiceOverlay } from "./VoiceOverlay";
export { VoiceSettingsSection } from "./VoiceSettingsSection";
export { MicPermissionModal } from "./MicPermissionModal";
export { TtsReadAlong } from "./TtsReadAlong";
export { useSpokenReplies } from "./useSpokenReplies";
export { speakReply } from "./speakReply";
export { ttsEngine } from "./ttsEngine";
export type { TtsProgress, TtsState } from "./ttsEngine";
export {
  getAudioSettings,
  loadAudioSettings,
  saveAudioSettings,
  effectivePlaybackVolume,
} from "./audioSettings";
export { requestMicAccess, micAccessAllowed } from "./micPermission";
export {
  createBatchTranscriptionSession,
  createStreamingTranscriptionSession,
  resampleLinear,
} from "./transcriptionSession";
