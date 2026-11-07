//! When the launcher asks WebView2 to keep less memory (`SetMemoryUsageTargetLevel`): the
//! Python window's soft cap does not run under the launcher, and an idle WebView2 grew to
//! 2.7-3.6 GB.

/// No input for this long counts as idle.
pub const IDLE_S: u64 = 600;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Level {
    Normal,
    Low,
}

/// Low while the window is minimized, hidden (tray) or the person has been away
/// [`IDLE_S`]; Normal otherwise.
pub fn level(minimized: bool, hidden: bool, idle_s: u64) -> Level {
    if minimized || hidden || idle_s >= IDLE_S {
        Level::Low
    } else {
        Level::Normal
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn low_when_minimized_hidden_or_idle() {
        assert_eq!(level(false, false, 0), Level::Normal);
        assert_eq!(level(false, false, IDLE_S - 1), Level::Normal);
        assert_eq!(level(false, false, IDLE_S), Level::Low);
        assert_eq!(level(true, false, 0), Level::Low);
        assert_eq!(level(false, true, 0), Level::Low);
        // Minimized 10 minutes.
        assert_eq!(level(true, false, 600), Level::Low);
    }
}
