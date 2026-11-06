#![cfg_attr(not(test), windows_subsystem = "windows")]

use std::process::ExitCode;

use ducky_launcher::argv::{self, Command};

fn main() -> ExitCode {
    match argv::parse(std::env::args_os()) {
        Command::Version => {
            ducky_launcher::console::print_line(ducky_launcher::APP_VERSION);
            ExitCode::SUCCESS
        }
        Command::Launch(args) => ExitCode::from(ducky_launcher::run(args)),
    }
}
