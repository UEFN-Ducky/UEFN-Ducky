//! Sealing for this OS user plus a second secret, as the Python backend does it
//! (`backend/agent/secrets.py`, `backend/uefn_plugins/data_crypto.py`): a value sealed by
//! one backend opens in the other.
//!
//! - Windows: DPAPI (`CryptProtectData`), the second secret as its optional entropy.
//! - Linux and macOS: `version(1) | nonce(12) | AES-256-GCM`, under the master key, the
//!   second secret bound as associated data.
//!
//! Stored text is `enc1:` + base64; stored bytes are `UDE1` + the sealed bytes. Every
//! call here blocks (a system call, about 0.3 ms): only inside `spawn_blocking` or a
//! store closure.

use aes_gcm::aead::{Aead, AeadCore, KeyInit, OsRng, Payload};
use aes_gcm::{Aes256Gcm, Nonce};
use base64::engine::general_purpose::STANDARD as BASE64;
use base64::Engine;

use super::{Error, Result};

/// A sealed text value starts with this (`data_crypto.PREFIX`).
pub const PREFIX: &str = "enc1:";
/// A sealed file or payload starts with this (`data_crypto.FILE_MAGIC`).
pub const FILE_MAGIC: &[u8] = b"UDE1";
const ENTROPY: &[u8] = b"UEFN-Ducky-v1-credentials";
const SEAL_VERSION: u8 = 1;

/// The second secret of an account's data (`data_crypto._entropy`): none signed out,
/// the account data key signed in.
pub fn account_entropy(data_key: Option<&[u8; 32]>) -> Vec<u8> {
    match data_key {
        None => b"local".to_vec(),
        Some(key) => [b"adk:", &key[..]].concat(),
    }
}

/// Seal for this OS user; with `entropy`, both are needed to open it again.
pub fn protect(data: &[u8], entropy: Option<&[u8]>) -> Result<Vec<u8>> {
    os_seal(data, entropy, true)
}

/// Inverse of [`protect`]; an error on the wrong user, key or entropy.
pub fn unprotect(blob: &[u8], entropy: Option<&[u8]>) -> Result<Vec<u8>> {
    os_seal(blob, entropy, false)
}

pub fn is_sealed(stored: &str) -> bool {
    stored.starts_with(PREFIX)
}

pub fn seal_text(text: &str, entropy: &[u8]) -> Result<String> {
    Ok(format!("{PREFIX}{}", BASE64.encode(protect(text.as_bytes(), Some(entropy))?)))
}

pub fn open_text(stored: &str, entropy: &[u8]) -> Result<String> {
    let sealed = stored.strip_prefix(PREFIX).ok_or_else(|| Error("value is not sealed".into()))?;
    let blob = BASE64.decode(sealed).map_err(|_| Error("sealed value is not base64".into()))?;
    String::from_utf8(unprotect(&blob, Some(entropy))?).map_err(|_| Error("sealed value is not text".into()))
}

pub fn seal_bytes(data: &[u8], entropy: &[u8]) -> Result<Vec<u8>> {
    Ok([FILE_MAGIC, &protect(data, Some(entropy))?].concat())
}

pub fn open_bytes(raw: &[u8], entropy: &[u8]) -> Result<Vec<u8>> {
    let sealed = raw.strip_prefix(FILE_MAGIC).ok_or_else(|| Error("file is not sealed".into()))?;
    unprotect(sealed, Some(entropy))
}

#[cfg(windows)]
fn os_seal(data: &[u8], entropy: Option<&[u8]>, protect: bool) -> Result<Vec<u8>> {
    use std::ptr::{null, null_mut};
    use windows_sys::Win32::Foundation::LocalFree;
    use windows_sys::Win32::Security::Cryptography::{
        CryptProtectData, CryptUnprotectData, CRYPTPROTECT_UI_FORBIDDEN, CRYPT_INTEGER_BLOB,
    };

    let blob = |bytes: &[u8]| CRYPT_INTEGER_BLOB { cbData: bytes.len() as u32, pbData: bytes.as_ptr() as *mut u8 };
    // No entropy at all: plain user DPAPI, the way Chromium seals its cookie key.
    let salted = entropy.map(|e| [ENTROPY, e].concat());
    let (input, salt) = (blob(data), salted.as_deref().map(blob));
    let salt = salt.as_ref().map_or(null(), |b| b as *const CRYPT_INTEGER_BLOB);
    let mut out = CRYPT_INTEGER_BLOB { cbData: 0, pbData: null_mut() };
    // SAFETY: `input` and `salt` point at slices that outlive the call; DPAPI only reads
    // them and writes `out`, which is ours to free.
    let ok = unsafe {
        if protect {
            CryptProtectData(&input, null(), salt, null(), null(), CRYPTPROTECT_UI_FORBIDDEN, &mut out)
        } else {
            CryptUnprotectData(&input, null_mut(), salt, null(), null(), CRYPTPROTECT_UI_FORBIDDEN, &mut out)
        }
    };
    if ok == 0 {
        return Err(Error(if protect { "CryptProtectData failed" } else { "CryptUnprotectData failed" }.into()));
    }
    if out.pbData.is_null() {
        return Ok(Vec::new());
    }
    // SAFETY: DPAPI returned `cbData` bytes at `pbData` in a LocalAlloc buffer; copied,
    // then freed once (kept, every sealed read and write leaks its whole text).
    let bytes = unsafe { std::slice::from_raw_parts(out.pbData, out.cbData as usize) }.to_vec();
    unsafe { LocalFree(out.pbData.cast()) };
    Ok(bytes)
}

#[cfg(not(windows))]
fn os_seal(data: &[u8], entropy: Option<&[u8]>, protect: bool) -> Result<Vec<u8>> {
    let key = master_key()?;
    if protect {
        aead_seal(&key, data, entropy)
    } else {
        aead_open(&key, data, entropy)
    }
}

/// The master key file. Python keeps the key in the system keyring (Secret Service,
/// Keychain) and only falls back to this file; a file that exists always wins there.
/// ponytail: the keyring is not read here, and no file is ever made here: a new file
/// would win over the keyring in Python and lock away everything sealed with it. Read
/// the keyring the way Python's `keyring` does before a Linux or macOS path moves.
#[cfg(not(windows))]
fn master_key() -> Result<[u8; 32]> {
    static KEY: std::sync::OnceLock<Option<[u8; 32]>> = std::sync::OnceLock::new();
    #[allow(clippy::disallowed_methods)] // 32 bytes, once per process, on a blocking thread
    let read = || std::fs::read(crate::app_data_dir().join("master.key")).ok()?.try_into().ok();
    KEY.get_or_init(read).ok_or_else(|| Error("this PC's master key is in the system keyring, which is not read here yet".into()))
}

fn aad(entropy: Option<&[u8]>) -> Vec<u8> {
    match entropy {
        None => vec![0],
        Some(entropy) => [&[1u8][..], ENTROPY, entropy].concat(),
    }
}

#[cfg_attr(windows, allow(dead_code))]
fn aead_seal(key: &[u8; 32], data: &[u8], entropy: Option<&[u8]>) -> Result<Vec<u8>> {
    let nonce = Aes256Gcm::generate_nonce(&mut OsRng);
    let sealed = Aes256Gcm::new(key.into())
        .encrypt(&nonce, Payload { msg: data, aad: &aad(entropy) })
        .map_err(|_| Error("Seal failed".into()))?;
    Ok([&[SEAL_VERSION][..], nonce.as_slice(), &sealed].concat())
}

#[cfg_attr(windows, allow(dead_code))]
fn aead_open(key: &[u8; 32], blob: &[u8], entropy: Option<&[u8]>) -> Result<Vec<u8>> {
    if blob.len() < 29 || blob[0] != SEAL_VERSION {
        return Err(Error("Unseal failed: not a sealed blob".into()));
    }
    Aes256Gcm::new(key.into())
        .decrypt(Nonce::from_slice(&blob[1..13]), Payload { msg: &blob[13..], aad: &aad(entropy) })
        .map_err(|_| Error("Unseal failed".into()))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn hex(text: &str) -> Vec<u8> {
        (0..text.len()).step_by(2).map(|i| u8::from_str_radix(&text[i..i + 2], 16).unwrap()).collect()
    }

    /// Blobs made by Python's `_linux_seal` (cryptography's AESGCM), key 00..1f.
    #[test]
    fn the_linux_seal_opens_what_python_sealed() {
        let key: [u8; 32] = std::array::from_fn(|i| i as u8);
        let with_key = hex("016465666768696a6b6c6d6e6f20d8770a158676fa4b0134426465fe11fe3731216caa7d12550165");
        let entropy = account_entropy(Some(&[0; 32]));
        assert_eq!(aead_open(&key, &with_key, Some(&entropy)).unwrap(), "héllo duck".as_bytes());
        let plain = hex("016465666768696a6b6c6d6e6f3877bf0f175d8e2d89f9e14385debad16387d62c01");
        assert_eq!(aead_open(&key, &plain, None).unwrap(), b"plain");
        // The wrong second secret, a cut blob and another version all fail.
        assert_eq!(aead_open(&key, &with_key, Some(b"local")), Err(Error("Unseal failed".into())));
        assert_eq!(aead_open(&key, &with_key, None), Err(Error("Unseal failed".into())));
        assert!(aead_open(&key, &with_key[..28], Some(&entropy)).is_err());
        assert!(aead_open(&key, &[&[2u8][..], &with_key[1..]].concat(), Some(&entropy)).is_err());
    }

    #[test]
    fn the_linux_seal_round_trips_and_never_repeats() {
        let key = [7u8; 32];
        let (a, b) = (aead_seal(&key, b"duck", Some(b"local")).unwrap(), aead_seal(&key, b"duck", Some(b"local")).unwrap());
        assert!(a != b && a.len() == 1 + 12 + 4 + 16 && a[0] == 1);
        assert_eq!(aead_open(&key, &a, Some(b"local")).unwrap(), b"duck");
        assert!(aead_open(&[8u8; 32], &a, Some(b"local")).is_err());
    }

    #[test]
    fn text_and_bytes_round_trip_on_this_os() {
        let (local, account) = (account_entropy(None), account_entropy(Some(&[9; 32])));
        let sealed = seal_text("héllo 🦆", &account).unwrap();
        assert!(is_sealed(&sealed) && !sealed.contains("llo"));
        assert_eq!(open_text(&sealed, &account).unwrap(), "héllo 🦆");
        assert!(open_text(&sealed, &local).is_err(), "another account's key must not open it");
        assert!(open_text("plain", &local).is_err());
        let file = seal_bytes(&[0, 1, 2, 255], &local).unwrap();
        assert!(file.starts_with(FILE_MAGIC));
        assert_eq!(open_bytes(&file, &local).unwrap(), [0, 1, 2, 255]);
        assert!(open_bytes(&[0, 1, 2], &local).is_err() && open_bytes(&file, &account).is_err());
        assert_eq!(open_text(&seal_text("", &local).unwrap(), &local).unwrap(), "");
    }
}
