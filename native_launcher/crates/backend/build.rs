//! Builds in ducky.db's schema steps (`ducky_app/backend/store/migrations/NNNN_name.sql`,
//! the files the Python backend applies) as `store::migrate::MIGRATIONS`.
#![allow(clippy::disallowed_methods)] // a build script: nothing waits on it

use std::path::PathBuf;
use std::{env, fs};

fn main() {
    let dir = PathBuf::from(env::var("CARGO_MANIFEST_DIR").unwrap()).join("../../../ducky_app/backend/store/migrations");
    println!("cargo:rerun-if-changed={}", dir.display());
    let listing = fs::read_dir(&dir).unwrap_or_else(|e| panic!("{}: {e}", dir.display()));
    let mut files: Vec<PathBuf> = listing.map(|entry| entry.unwrap().path()).filter(|p| p.extension().is_some_and(|x| x == "sql")).collect();
    files.sort();
    let mut out = String::from("&[\n");
    for (index, path) in files.iter().enumerate() {
        let name = path.file_name().unwrap().to_string_lossy();
        let number: usize = name.get(..4).and_then(|n| n.parse().ok()).unwrap_or(0);
        assert!(number == index + 1 && name[4..].starts_with('_'), "migrations must be numbered 0001..N without gaps: {name}");
        out += &format!("    ({number}, include_str!({:?})),\n", path.canonicalize().unwrap());
    }
    out += "]\n";
    fs::write(PathBuf::from(env::var("OUT_DIR").unwrap()).join("migrations.rs"), out).unwrap();
}
