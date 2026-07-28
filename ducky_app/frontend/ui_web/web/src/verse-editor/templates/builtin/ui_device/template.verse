# using { /Verse.org/Simulation }
# using { /Fortnite.com/Devices }
# using { /Fortnite.com/UI }
# using { /UnrealEngine.com/Temporary/UI }
# using { /Verse.org/Colors/NamedColors } 

# ui_device<epic_internal> := class(creative_device):

#     StringToMessage<localizes>(InString : string) : message = "{InString}"

#     OnBegin<override>():void =
#         Print("ui_device OnBegin", ?Duration:=6.0)

#     CreateHUDCanvas() : canvas =
#         canvas:
#             Slots := array:
#                 canvas_slot:
#                     Widget := text_block:
#                         DefaultText := StringToMessage("Hello UI")
#                         DefaultTextColor := NamedColors.White
