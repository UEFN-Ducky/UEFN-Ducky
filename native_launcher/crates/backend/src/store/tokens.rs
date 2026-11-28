//! Search without the words (`backend/store/account_rows.py`): a message's search text
//! is stored as one keyed token per word, and a query becomes the same tokens. The key
//! is the account's chat key, which also names stored payloads.

use blake2::digest::consts::{U32, U5};
use blake2::digest::Mac;
use blake2::Blake2bMac;
use unicode_normalization::char::canonical_combining_class;
use unicode_normalization::UnicodeNormalization;
use unicode_properties::{GeneralCategoryGroup, UnicodeGeneralCategory};

use super::{Error, Result};

/// An account's chat key: 1 to 64 bytes, as BLAKE2b takes them.
#[derive(Clone)]
pub struct Key(Vec<u8>);

impl Key {
    pub fn new(bytes: Vec<u8>) -> Result<Key> {
        if bytes.is_empty() || bytes.len() > 64 {
            return Err(Error("the chat key is damaged".into()));
        }
        Ok(Key(bytes))
    }

    pub fn as_bytes(&self) -> &[u8] {
        &self.0
    }
}

/// Python's `str.split()` pieces (its whitespace has four control characters more).
fn split(text: &str) -> impl Iterator<Item = &str> {
    text.split(|c: char| c.is_whitespace() || ('\x1c'..='\x1f').contains(&c)).filter(|piece| !piece.is_empty())
}

/// The words search matches on: case and accents folded, `_` and punctuation split.
/// ponytail: letters and digits are told by this build's Unicode tables, Python's by
/// its own; a character newer than either is a word break on one side only.
pub fn words(text: &str) -> Vec<String> {
    let folded = caseless::default_case_fold_str(text);
    let mut out = Vec::new();
    let mut word = String::new();
    for ch in folded.nfkd().filter(|ch| canonical_combining_class(*ch) == 0) {
        if matches!(ch.general_category_group(), GeneralCategoryGroup::Letter | GeneralCategoryGroup::Number) {
            word.push(ch);
        } else if !word.is_empty() {
            out.push(std::mem::take(&mut word));
        }
    }
    if !word.is_empty() {
        out.push(word);
    }
    out
}

fn token(word: &str, key: &Key) -> String {
    // Key::new checked the length, the only thing this refuses.
    let Ok(mut mac) = Blake2bMac::<U5>::new_with_salt_and_personal(&key.0, &[], b"chat-word") else {
        return String::new();
    };
    mac.update(word.as_bytes());
    let bits = mac.finalize().into_bytes().iter().fold(0u64, |bits, byte| bits << 8 | u64::from(*byte));
    // Lowercase base32 of the 5 bytes: 8 characters, no padding.
    (0..8).rev().map(|i| b"abcdefghijklmnopqrstuvwxyz234567"[(bits >> (5 * i) & 31) as usize] as char).collect()
}

fn tokens<'a>(words: impl IntoIterator<Item = &'a String>, key: &Key) -> String {
    words.into_iter().map(|word| token(word, key)).collect::<Vec<_>>().join(" ")
}

/// What the search index keeps of `text`: one keyed token per word, in order.
pub fn blind_text(text: &str, key: &Key) -> String {
    tokens(&words(text), key)
}

/// FTS5 query over [`blind_text`] rows: every term as a phrase, or all as one.
pub fn blind_query(query: &str, key: &Key) -> String {
    let terms: Vec<Vec<String>> = split(query).map(words).filter(|words| !words.is_empty()).collect();
    let phrases = terms.iter().map(|words| format!("\"{}\"", tokens(words, key))).collect::<Vec<_>>().join(" ");
    if terms.len() > 1 {
        return format!("({phrases}) OR \"{}\"", tokens(terms.iter().flatten(), key));
    }
    phrases
}

/// Name of a stored payload: keyed, so two accounts never share one.
pub fn part_digest(text: &str, key: &Key) -> String {
    let Ok(mut mac) = Blake2bMac::<U32>::new_with_salt_and_personal(&key.0, &[], b"chat-part") else {
        return String::new();
    };
    mac.update(text.as_bytes());
    mac.finalize().into_bytes().iter().map(|byte| format!("{byte:02x}")).collect()
}
