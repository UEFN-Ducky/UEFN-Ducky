"""Device semantics catalog (Python module so packaging never drops the data)."""

from __future__ import annotations

from typing import Any

# Keep in sync with product docs / Tester skill — no separate JSON load at runtime.
DEVICE_SEMANTICS: dict[str, Any] = {
    "button_device": {
        "aliases": ["Button_Device", "BP_Creative_Button", "CreativeButton"],
        "emits": ["InteractedWithEvent", "PressedEvent"],
        "receives": ["Enable", "Disable"],
        "effects": [],
    },
    "trigger_device": {
        "aliases": ["Trigger_Device", "BP_Creative_Trigger", "CreativeTrigger"],
        "emits": ["TriggeredEvent"],
        "receives": ["Trigger", "Enable", "Disable"],
        "effects": [{"kind": "signal", "detail": "fires TriggeredEvent to wired listeners"}],
    },
    "item_granter_device": {
        "aliases": ["Item_Granter_Device", "BP_Creative_ItemGranter", "ItemGranter"],
        "emits": ["ItemGrantedEvent"],
        "receives": ["GrantItem", "GrantItemToAll"],
        "effects": [{"kind": "grant_item", "detail": "grants configured item to instigator"}],
    },
    "conditional_button_device": {
        "aliases": ["Conditional_Button_Device"],
        "emits": ["ActivatedEvent", "NotActivatedEvent"],
        "receives": ["Activate", "Enable", "Disable"],
        "effects": [],
    },
    "teleporter_device": {
        "aliases": ["Teleporter_Device", "BP_Creative_Teleporter"],
        "emits": ["TeleportedEvent"],
        "receives": ["Teleport", "Enable", "Disable"],
        "effects": [{"kind": "teleport", "detail": "moves player to teleporter / linked target"}],
    },
    "player_spawner_device": {
        "aliases": ["Player_Spawner_Device", "BP_PlayerSpawner", "PlayerSpawnPad"],
        "emits": ["SpawnedEvent"],
        "receives": ["Enable", "Disable", "Spawn"],
        "effects": [{"kind": "spawn", "detail": "spawns a player at this pad"}],
    },
    "score_manager_device": {
        "aliases": ["Score_Manager_Device", "ScoreManager"],
        "emits": ["ScoreUpdatedEvent", "ScoreReachedEvent"],
        "receives": ["Activate", "AddScore", "SetScore"],
        "effects": [{"kind": "score", "detail": "changes player/team score"}],
    },
    "hud_message_device": {
        "aliases": ["HUD_Message_Device", "HUDMessage"],
        "emits": [],
        "receives": ["Show", "Hide"],
        "effects": [{"kind": "hud", "detail": "shows HUD message to player"}],
    },
    "timer_device": {
        "aliases": ["Timer_Device", "BP_Creative_Timer"],
        "emits": ["SuccessEvent", "FailureEvent"],
        "receives": ["Start", "Stop", "Reset"],
        "effects": [{"kind": "timer", "detail": "starts/stops a countdown"}],
    },
    "movement_modulator_device": {
        "aliases": ["Movement_Modulator_Device", "MovementModulator"],
        "emits": [],
        "receives": ["Enable", "Disable", "Activate"],
        "effects": [{"kind": "movement", "detail": "modifies player movement (speed/jump/etc.)"}],
    },
    "volume_device": {
        "aliases": ["volume_device", "Volume_Device"],
        "emits": ["AgentEntersEvent", "AgentExitsEvent"],
        "receives": ["Enable", "Disable"],
        "effects": [],
    },
    "cinematic_sequence_device": {
        "aliases": ["Cinematic_Sequence_Device", "CinematicSequence"],
        "emits": ["StoppedEvent", "EndedEvent"],
        "receives": ["Play", "Stop", "Pause"],
        "effects": [{"kind": "cinematic", "detail": "plays assigned Level Sequence"}],
    },
    "verse_script": {
        "aliases": ["VerseDevice_C"],
        "emits": ["*"],
        "receives": ["*"],
        "effects": [
            {"kind": "verse", "detail": "custom Verse logic — inspect source for exact behavior"}
        ],
    },
}
