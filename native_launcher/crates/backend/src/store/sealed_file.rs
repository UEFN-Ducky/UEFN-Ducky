//! One file sealed for a Ducky account (`UDF1`), as `backend/store/sealed_files.py`
//! writes and reads it.
//!
//! A file is sealed with its own random key (AES-256-GCM, 1 MiB chunks, the last one
//! marked so a cut file never opens). That key is wrapped for the account with X25519:
//! anyone can seal for an account (with its public key), only the account can open.
//! Sealing and opening write a new file beside the old one and swap it in, keeping the
//! file's times, so an interrupted run leaves the original whole.
//!
//! Every function here blocks (file reads and writes): callers run them on a blocking
//! thread, never on an async worker.
#![allow(clippy::disallowed_methods)]

use std::fs::{self, File, FileTimes, Metadata, OpenOptions};
use std::io::{self, Read, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{Duration, Instant};

use aes_gcm::aead::rand_core::RngCore;
use aes_gcm::aead::{Aead, KeyInit, OsRng, Payload};
use aes_gcm::{Aes256Gcm, Nonce};
use base64::engine::general_purpose::STANDARD as BASE64;
use base64::Engine;
use hkdf::Hkdf;
use serde_json::{json, Value};
use sha2::Sha256;
use x25519_dalek::{PublicKey, StaticSecret};

use super::pyjson::dumps;
use super::{Error, Result};

pub const MAGIC: &[u8; 4] = b"UDF1";
const CHUNK: usize = 1024 * 1024;
/// A copy being written; swept if a crash leaves it.
pub const TMP_SUFFIX: &str = ".udf-tmp";
const INFO: &[u8] = b"ducky-file-key/1";
/// No header is near this: a damaged length never becomes a huge read.
const HEADER_MAX: usize = 64 * 1024;
pub const WRONG_ACCOUNT: &str = "This file is locked to another Ducky account. Sign in with that account to open it.";

fn bad(what: &str) -> Error {
    Error(what.to_string())
}

pub(super) fn random<const N: usize>() -> [u8; N] {
    let mut out = [0u8; N];
    OsRng.fill_bytes(&mut out);
    out
}

/// A new private key and its public key.
pub fn keypair() -> ([u8; 32], [u8; 32]) {
    let private = StaticSecret::from(random::<32>());
    (private.to_bytes(), PublicKey::from(&private).to_bytes())
}

fn cipher(key: &[u8]) -> Result<Aes256Gcm> {
    Aes256Gcm::new_from_slice(key).map_err(|_| bad("a file key is not 32 bytes"))
}

fn kek(shared: &[u8; 32], epk: &[u8; 32], public: &[u8; 32]) -> [u8; 32] {
    let mut out = [0u8; 32];
    // 32 bytes is a length HKDF-SHA256 always gives.
    let _ = Hkdf::<Sha256>::new(None, shared).expand(&[INFO, epk, public].concat(), &mut out);
    out
}

/// The header fields that hand `file_key` to the account: (`epk`, `wrapped`).
fn wrap(file_key: &[u8; 32], account: &str, public: &[u8; 32]) -> Result<(String, String)> {
    let ephemeral = StaticSecret::from(random::<32>());
    let epk = PublicKey::from(&ephemeral).to_bytes();
    let shared = ephemeral.diffie_hellman(&PublicKey::from(*public));
    let nonce = random::<12>();
    let sealed = cipher(&kek(shared.as_bytes(), &epk, public))?
        .encrypt(Nonce::from_slice(&nonce), Payload { msg: file_key, aad: account.as_bytes() })
        .map_err(|_| bad("the file key could not be wrapped"))?;
    Ok((BASE64.encode(epk), BASE64.encode([nonce.as_slice(), &sealed].concat())))
}

fn unwrap(header: &Value, account: &str, private: &StaticSecret) -> Result<Vec<u8>> {
    let field = |name: &str| BASE64.decode(header[name].as_str().unwrap_or_default()).map_err(|_| bad("sealed file has a damaged header"));
    let epk: [u8; 32] = field("epk")?.try_into().map_err(|_| bad("sealed file has a damaged header"))?;
    let wrapped = field("wrapped")?;
    if wrapped.len() < 12 {
        return Err(bad("sealed file has a damaged header"));
    }
    let shared = private.diffie_hellman(&PublicKey::from(epk));
    let kek = kek(shared.as_bytes(), &epk, &PublicKey::from(private).to_bytes());
    cipher(&kek)?
        .decrypt(Nonce::from_slice(&wrapped[..12]), Payload { msg: &wrapped[12..], aad: account.as_bytes() })
        .map_err(|_| bad("this account's file key does not open the file"))
}

pub fn is_sealed_file(path: &Path) -> bool {
    let mut magic = [0u8; 4];
    File::open(path).and_then(|mut file| file.read_exact(&mut magic)).is_ok() && magic == *MAGIC
}

/// The header as JSON, and as the bytes every chunk is bound to.
fn read_header(src: &mut impl Read) -> Result<(Value, Vec<u8>)> {
    let mut head = [0u8; 8];
    if src.read_exact(&mut head).is_err() || head[..4] != *MAGIC {
        return Err(bad("file is not sealed"));
    }
    let size = u32::from_be_bytes([head[4], head[5], head[6], head[7]]) as usize;
    let mut raw = vec![0u8; size.min(HEADER_MAX)];
    if size > HEADER_MAX || src.read_exact(&mut raw).is_err() {
        return Err(bad("sealed file has a damaged header"));
    }
    let header = serde_json::from_slice(&raw).map_err(|_| bad("sealed file has a damaged header"))?;
    Ok((header, raw))
}

/// The account a sealed file is for, `None` for a plain one.
pub fn sealed_for(path: &Path) -> Option<String> {
    let (header, _) = read_header(&mut File::open(path).ok()?).ok()?;
    Some(header["account"].as_str().unwrap_or_default().to_string())
}

fn nonce(i: u64) -> [u8; 12] {
    let mut out = [0u8; 12];
    out[4..].copy_from_slice(&i.to_be_bytes());
    out
}

/// The flag, the chunk's number and the header are authenticated, so chunks can't be
/// dropped or moved.
fn aad(header_raw: &[u8], i: u64, last: bool) -> Vec<u8> {
    [header_raw, &i.to_be_bytes(), &[last as u8]].concat()
}

fn fill(src: &mut impl Read, chunk: &mut Vec<u8>) -> io::Result<()> {
    chunk.clear();
    src.take(CHUNK as u64).read_to_end(chunk).map(|_| ())
}

/// Each chunk: last-chunk flag (1 byte), ciphertext length (4), ciphertext.
fn encrypt_stream(src: &mut impl Read, dst: &mut impl Write, file_key: &[u8], header_raw: &[u8]) -> Result<()> {
    let sealer = cipher(file_key)?;
    let (mut chunk, mut following) = (Vec::new(), Vec::new());
    fill(src, &mut chunk)?;
    for i in 0u64.. {
        following.clear();
        if chunk.len() == CHUNK {
            fill(src, &mut following)?;
        }
        let last = following.is_empty();
        let sealed = sealer
            .encrypt(Nonce::from_slice(&nonce(i)), Payload { msg: &chunk, aad: &aad(header_raw, i, last) })
            .map_err(|_| bad("the file could not be sealed"))?;
        dst.write_all(&[last as u8])?;
        dst.write_all(&(sealed.len() as u32).to_be_bytes())?;
        dst.write_all(&sealed)?;
        if last {
            break;
        }
        std::mem::swap(&mut chunk, &mut following);
    }
    Ok(())
}

fn decrypt_stream(src: &mut impl Read, file_key: &[u8], header_raw: &[u8], mut plain: impl FnMut(&[u8]) -> io::Result<()>) -> Result<()> {
    let opener = cipher(file_key)?;
    for i in 0u64.. {
        let mut head = [0u8; 5];
        src.read_exact(&mut head).map_err(|_| bad("sealed file is cut short"))?;
        let (last, size) = (head[0] != 0, u32::from_be_bytes([head[1], head[2], head[3], head[4]]) as usize);
        // A chunk is never larger than this (its plaintext and the tag).
        let mut body = vec![0u8; size.min(CHUNK + 16)];
        if size > CHUNK + 16 || src.read_exact(&mut body).is_err() {
            return Err(bad("sealed file is cut short"));
        }
        let opened = opener
            .decrypt(Nonce::from_slice(&nonce(i)), Payload { msg: &body, aad: &aad(header_raw, i, last) })
            .map_err(|_| bad("sealed file is damaged"))?;
        plain(&opened)?;
        if last {
            if src.read(&mut [0u8; 1])? != 0 {
                return Err(bad("sealed file has bytes after its end"));
            }
            break;
        }
    }
    Ok(())
}

static TMP_SEQ: AtomicU64 = AtomicU64::new(0);

pub fn tmp_for(path: &Path) -> PathBuf {
    let name = path.file_name().unwrap_or_default().to_string_lossy();
    path.with_file_name(format!(".{name}.{}.{}{TMP_SUFFIX}", std::process::id(), TMP_SEQ.fetch_add(1, Ordering::Relaxed)))
}

/// A rename, waiting a moment while another reader still has `dest` open (on Windows
/// the swap fails then; two openers of one file finish within milliseconds).
pub fn replace(tmp: &Path, dest: &Path) -> io::Result<()> {
    let deadline = Instant::now() + Duration::from_secs(2);
    loop {
        match fs::rename(tmp, dest) {
            Err(err) if err.kind() == io::ErrorKind::PermissionDenied && Instant::now() < deadline => {
                std::thread::sleep(Duration::from_millis(50));
            }
            done => return done,
        }
    }
}

pub(super) fn keep_times(path: &Path, times: &Metadata) {
    let (Ok(accessed), Ok(modified)) = (times.accessed(), times.modified()) else { return };
    if let Ok(file) = OpenOptions::new().write(true).open(path) {
        let _ = file.set_times(FileTimes::new().set_accessed(accessed).set_modified(modified));
    }
}

/// Write a new file through `fill` and swap it in for `dest`, whole or not at all.
fn swap_in(dest: &Path, fill: impl FnOnce(&mut File) -> Result<()>) -> Result<()> {
    let tmp = tmp_for(dest);
    let done = (|| {
        let mut out = File::create(&tmp)?;
        fill(&mut out)?;
        out.sync_all()?;
        drop(out);
        Ok(replace(&tmp, dest)?)
    })();
    let _ = fs::remove_file(&tmp);
    done
}

/// Seal `path` in place for `account`. False when it already is sealed.
pub fn seal_file(path: &Path, account: &str, public: &[u8; 32]) -> Result<bool> {
    if is_sealed_file(path) {
        return Ok(false);
    }
    let times = fs::metadata(path)?;
    let file_key = random::<32>();
    let (epk, wrapped) = wrap(&file_key, account, public)?;
    // Spaced as Python's json.dumps spaces it: a file is the same size whoever sealed it.
    let header_raw = dumps(&json!({"v": 1, "account": account, "epk": epk, "wrapped": wrapped})).into_bytes();
    swap_in(path, |out| {
        out.write_all(MAGIC)?;
        out.write_all(&(header_raw.len() as u32).to_be_bytes())?;
        out.write_all(&header_raw)?;
        encrypt_stream(&mut File::open(path)?, out, &file_key, &header_raw)
    })?;
    keep_times(path, &times);
    Ok(true)
}

/// The plaintext of sealed `path`, handed to `plain` chunk by chunk. `account` is whose
/// private key `private` is: a file for anyone else stays shut.
fn opened(path: &Path, account: &str, private: &StaticSecret, plain: impl FnMut(&[u8]) -> io::Result<()>) -> Result<()> {
    let mut src = File::open(path)?;
    let (header, raw) = read_header(&mut src)?;
    if header["account"].as_str().unwrap_or_default() != account {
        return Err(bad(WRONG_ACCOUNT));
    }
    decrypt_stream(&mut src, &unwrap(&header, account, private)?, &raw, plain)
}

/// Write the plaintext of sealed `path` to `dest` (a new file swapped in whole).
pub fn open_to(path: &Path, dest: &Path, account: &str, private: &StaticSecret) -> Result<()> {
    swap_in(dest, |out| opened(path, account, private, |part| out.write_all(part)))
}

/// Open sealed `path` in place. False when it was not sealed.
pub fn open_file(path: &Path, account: &str, private: &StaticSecret) -> Result<bool> {
    if !is_sealed_file(path) {
        return Ok(false);
    }
    let times = fs::metadata(path)?;
    open_to(path, path, account, private)?;
    keep_times(path, &times);
    Ok(true)
}

/// A sealed file's contents.
pub fn read_sealed(path: &Path, account: &str, private: &StaticSecret) -> Result<Vec<u8>> {
    let mut out = Vec::new();
    opened(path, account, private, |part| {
        out.extend_from_slice(part);
        Ok(())
    })?;
    Ok(out)
}

/// Every file under `folder` that `keep` wants, however deep.
pub fn walk(folder: &Path, keep: &dyn Fn(&Path) -> bool, out: &mut Vec<PathBuf>) {
    for entry in fs::read_dir(folder).into_iter().flatten().flatten() {
        let path = entry.path();
        if entry.file_type().is_ok_and(|kind| kind.is_dir()) {
            walk(&path, keep, out);
        } else if path.is_file() && keep(&path) {
            out.push(path);
        }
    }
}

pub fn is_tmp(path: &Path) -> bool {
    path.file_name().is_some_and(|name| name.to_string_lossy().ends_with(TMP_SUFFIX))
}

/// A folder's files, without copies still being written.
pub fn files(folder: &Path) -> Vec<PathBuf> {
    let mut out = Vec::new();
    walk(folder, &|path| !is_tmp(path), &mut out);
    out
}

pub fn read_plain(path: &Path) -> Result<Vec<u8>> {
    Ok(fs::read(path)?)
}

/// Replace `path` with `data`, whole or not at all.
pub fn write_whole(path: &Path, data: &[u8]) -> Result<()> {
    swap_in(path, |out| Ok(out.write_all(data)?))
}

/// (chat id, its attachments folder) of every chat that has one on disk.
pub fn chat_folders(root: &Path) -> Vec<(String, PathBuf)> {
    let dirs = |at: PathBuf| fs::read_dir(at).into_iter().flatten().flatten().map(|entry| entry.path());
    let mut out = Vec::new();
    for chat in dirs(root.join("chats").join("projects")).flat_map(|project| dirs(project.join("conversations"))) {
        let attachments = chat.join("attachments");
        if let (true, Some(name)) = (attachments.is_dir(), chat.file_name()) {
            out.push((name.to_string_lossy().into_owned(), attachments));
        }
    }
    out
}

/// Plain copies an interrupted open left behind (a crash, or the app closed mid-open):
/// `*.udf-tmp` beside ducky.db, its snapshots and chat attachments, and integrity-check
/// copies of sealed backups. One still being written is newer than `older_than_s`.
pub fn sweep_leftovers(root: &Path, older_than_s: f64) -> usize {
    let name = |path: &Path| path.file_name().unwrap_or_default().to_string_lossy().into_owned();
    let direct = |folder: PathBuf, keep: &dyn Fn(&str) -> bool| -> Vec<PathBuf> {
        fs::read_dir(folder).into_iter().flatten().flatten().map(|entry| entry.path()).filter(|path| keep(&name(path))).collect()
    };
    let mut places = direct(root.to_path_buf(), &|name| name.ends_with(TMP_SUFFIX));
    places.extend(direct(root.join("snapshots"), &|name| {
        name.ends_with(TMP_SUFFIX) || (name.starts_with('.') && name[1..].contains(".check.db"))
    }));
    for (_, folder) in chat_folders(root) {
        walk(&folder, &is_tmp, &mut places);
    }
    let old = |path: &PathBuf| {
        let age = fs::metadata(path).and_then(|meta| meta.modified()).ok().and_then(|at| at.elapsed().ok());
        age.is_some_and(|age| age.as_secs_f64() >= older_than_s)
    };
    places.iter().filter(|path| old(path) && fs::remove_file(path).is_ok()).count()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_stream_opens_as_it_was_sealed_and_never_when_cut() {
        let key = [7u8; 32];
        for size in [0, 1, CHUNK - 1, CHUNK, CHUNK + 1, 2 * CHUNK] {
            let plain: Vec<u8> = (0..size).map(|i| (i % 251) as u8).collect();
            let mut sealed = Vec::new();
            encrypt_stream(&mut plain.as_slice(), &mut sealed, &key, b"header").unwrap();
            let open = |sealed: &[u8], header: &[u8]| {
                let mut out = Vec::new();
                decrypt_stream(&mut &*sealed, &key, header, |part| {
                    out.extend_from_slice(part);
                    Ok(())
                })
                .map(|()| out)
            };
            assert_eq!(open(&sealed, b"header").unwrap(), plain);
            assert!(open(&sealed, b"other").is_err());
            assert!(open(&sealed[..sealed.len() - 1], b"header").is_err());
            assert!(open(&[sealed.as_slice(), b"x"].concat(), b"header").is_err());
            if size > CHUNK {
                // The first chunk alone is not a file: it is not marked last.
                assert!(open(&sealed[..5 + CHUNK + 16], b"header").is_err());
            }
        }
    }

    #[test]
    fn a_wrapped_key_opens_only_for_its_account() {
        let (private, public) = keypair();
        let private = StaticSecret::from(private);
        let (epk, wrapped) = wrap(&[9u8; 32], "a_1", &public).unwrap();
        let header = json!({"epk": epk, "wrapped": wrapped});
        assert_eq!(unwrap(&header, "a_1", &private).unwrap(), vec![9u8; 32]);
        assert!(unwrap(&header, "a_2", &private).is_err());
        assert!(unwrap(&header, "a_1", &StaticSecret::from(keypair().0)).is_err());
    }
}
