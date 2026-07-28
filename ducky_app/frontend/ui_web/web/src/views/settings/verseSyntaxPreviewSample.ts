/** Static Verse sample covering every Monarch syntax token for the appearance preview. */
export const VERSE_SYNTAX_PREVIEW_SAMPLE = `# Module demo — syntax preview (comments)

using { /Verse.org/Simulation }
using { /Fortnite.com/Devices }

PlayerDevices <public> := module:
    WalletSystem <public> := module:

# Type aliases and structs
timer_started<public> := type{_(CurrentTime: float):void}

timer_ui := struct:
    Canvas : canvas
    Text   : text_block

# Class with attributes, vars, and effects
demo_device <public> := class(creative_device):
    @editable var TimerDuration : float = 15.0
    @editable CountDown : logic = true
    var CurrentTime : float = 0.0
    var PlayersWithUI : [agent]timer_ui = map{}

    StringToMessage<localizes>(InString : string) : message = "{InString}"

    OnBegin<override>()<suspends> : void =
        if (CountDown?):
            spawn { RunLoop() }
        else:
            Print("Timer idle")

    RunLoop()<suspends><transacts> : void =
        for (I := 0..42):
            set CurrentTime = I * 3.14 + 1.0
            if (CurrentTime >= TimerDuration):
                break

    # Logical / comparison operators and a char literal
    CheckState(X : int) : logic =
        Grade : char = 'A'
        return X > 0 and X < 10 or not (X = 5)

    FormatGreeting(Name : string) : string =
        return "Hello\\n{Name}"

    BadString : string = "unclosed string
`;
