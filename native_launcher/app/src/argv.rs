//! Command line: `--version`, else every argument passes through to the backend unchanged.

use std::ffi::{OsStr, OsString};

pub const VERSION_FLAG: &str = "--version";
pub const BACKEND_FLAG: &str = "--backend";

#[derive(Debug, PartialEq, Eq)]
pub enum Command {
    /// Print the app version, open no window.
    Version,
    /// A normal launch: the user's arguments, argv[0] removed, order kept.
    Launch(Vec<OsString>),
}

/// Read the full argv (argv[0] first, as `std::env::args_os` gives it).
/// `--version` counts only as the first argument, so a file or link is never taken for it.
pub fn parse<I: IntoIterator<Item = OsString>>(argv: I) -> Command {
    let rest: Vec<OsString> = argv.into_iter().skip(1).collect();
    if rest.first().map(|a| a.as_os_str()) == Some(OsStr::new(VERSION_FLAG)) {
        Command::Version
    } else {
        Command::Launch(rest)
    }
}

/// The backend's arguments: `--backend` then the user's arguments in their order, untouched
/// (non-UTF-8 included).
pub fn backend_argv(args: &[OsString]) -> Vec<OsString> {
    let mut out = Vec::with_capacity(args.len() + 1);
    out.push(OsString::from(BACKEND_FLAG));
    out.extend(args.iter().cloned());
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    fn os(v: &[&str]) -> Vec<OsString> {
        v.iter().map(OsString::from).collect()
    }

    #[test]
    fn launch_keeps_every_argument_in_order() {
        let argv = os(&["Ducky-Launcher.exe", "--port", "4200", "C:/a b/x.verse", "--background", "uefn-ducky://store/x"]);
        let Command::Launch(args) = parse(argv) else { panic!("expected a launch") };
        assert_eq!(args, os(&["--port", "4200", "C:/a b/x.verse", "--background", "uefn-ducky://store/x"]));
        assert_eq!(
            backend_argv(&args),
            os(&["--backend", "--port", "4200", "C:/a b/x.verse", "--background", "uefn-ducky://store/x"])
        );
    }

    #[test]
    fn no_arguments_is_a_plain_launch() {
        assert_eq!(parse(os(&["Ducky-Launcher.exe"])), Command::Launch(vec![]));
        assert_eq!(parse(Vec::<OsString>::new()), Command::Launch(vec![]));
        assert_eq!(backend_argv(&[]), os(&["--backend"]));
    }

    #[test]
    fn version_only_as_first_argument() {
        assert_eq!(parse(os(&["x.exe", "--version"])), Command::Version);
        assert_eq!(parse(os(&["x.exe", "--version", "extra"])), Command::Version);
        assert_eq!(
            parse(os(&["x.exe", "C:/file.txt", "--version"])),
            Command::Launch(os(&["C:/file.txt", "--version"]))
        );
    }

    #[cfg(windows)]
    #[test]
    fn non_utf8_arguments_round_trip() {
        use std::os::windows::ffi::{OsStrExt, OsStringExt};
        let lone_surrogate = OsString::from_wide(&[0x0043, 0xD800, 0x0044]);
        let argv = vec![OsString::from("x.exe"), lone_surrogate.clone()];
        let Command::Launch(args) = parse(argv) else { panic!("expected a launch") };
        let back = backend_argv(&args);
        assert_eq!(back[1].encode_wide().collect::<Vec<_>>(), lone_surrogate.encode_wide().collect::<Vec<_>>());
    }
}
