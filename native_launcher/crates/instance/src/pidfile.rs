//! `panel.pid`: the panel's process id as plain text (`frozen_process.claim_panel_process`
//! writes `str(os.getpid())`). Read only; the launcher never writes or clears it.

use std::path::Path;

/// The pid in `panel.pid`, or None when it is missing, unreadable or not a positive number
/// (`int(text.strip())`, as `open_files.read_living_panel_pid` reads it).
pub fn read_panel_pid(path: &Path) -> Option<u32> {
    parse_panel_pid(&std::fs::read_to_string(path).ok()?)
}

pub fn parse_panel_pid(text: &str) -> Option<u32> {
    let t = text.trim().trim_start_matches('\u{feff}');
    let t = t.strip_prefix('+').unwrap_or(t);
    t.parse::<u32>().ok().filter(|pid| *pid > 0)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn old_format_plain_numbers() {
        assert_eq!(parse_panel_pid("4242"), Some(4242));
        assert_eq!(parse_panel_pid("4242\n"), Some(4242));
        assert_eq!(parse_panel_pid(" 4242\r\n"), Some(4242));
        assert_eq!(parse_panel_pid("+4242"), Some(4242));
    }

    #[test]
    fn junk_and_non_positive_are_none() {
        for bad in ["", "abc", "0", "-5", "42 43", "{\"pid\": 1}"] {
            assert_eq!(parse_panel_pid(bad), None, "{bad:?}");
        }
    }

    #[test]
    fn missing_file_is_none() {
        assert_eq!(read_panel_pid(Path::new("Z:/no/such/dir/panel.pid")), None);
    }
}
