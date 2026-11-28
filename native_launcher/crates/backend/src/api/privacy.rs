//! Personal data out of text before it is stored or sent (`backend/util/privacy.py`):
//! the person's folders, emails, keys and tokens, passwords, signed links and network
//! addresses. The rest of the line stays readable.
//!
//! The rules are Python's, one for one and in its order. Two of them look around a match
//! there, which this engine cannot: [`replace_unless`] does that part by hand.
//! ponytail: a "word" edge next to a combining mark is read as this engine reads it
//! (a mark is part of the word; Python's is not).

use std::sync::LazyLock;

use regex::{Captures, NoExpand, Regex};

const REDACTED: &str = "[redacted]";

fn rule(pattern: &str) -> Regex {
    Regex::new(pattern).expect("a fixed pattern")
}

/// Token shapes that are secrets on their own, wherever they appear.
static TOKENS: LazyLock<Regex> = LazyLock::new(|| {
    rule(concat!(
        r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{8,}",       // OpenAI / Anthropic keys
        r"|\b[sr]k_(?:live|test)_[A-Za-z0-9]{16,}",     // Stripe secret / restricted keys
        r"|\b(?:ghp|gho|ghs|ghu|ghr)_[A-Za-z0-9]{20,}", // GitHub tokens
        r"|\bgithub_pat_[A-Za-z0-9_]{20,}",
        r"|\bxox[abprs]-[A-Za-z0-9-]{10,}", // Slack
        r"|\bAKIA[0-9A-Z]{16}\b",           // AWS access key id
        r"|\bAIza[0-9A-Za-z_-]{35}",        // Google API key
        r"|\bxai-[A-Za-z0-9]{20,}",         // xAI
        r"|\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}", // JWT
        r"|\b[MN][A-Za-z0-9]{23,25}\.[A-Za-z0-9_-]{6}\.[A-Za-z0-9_-]{27,}", // Discord bot token
    ))
});
/// "Authorization: Bearer x", "Basic x".
static AUTH_SCHEME: LazyLock<Regex> = LazyLock::new(|| rule(r"(?i)\b(Bearer|Basic|Token)\s+[A-Za-z0-9._~+/=-]{8,}"));
/// key=value / key: value / "key": "value" for secret-looking names.
static SECRET_FIELD: LazyLock<Regex> = LazyLock::new(|| {
    rule(concat!(
        r#"(?i)(["']?\b(?:api[_-]?key|apikey|secret|client[_-]?secret|password|passwd|pwd|"#,
        r#"access[_-]?token|refresh[_-]?token|id[_-]?token|auth[_-]?token|token|session[_-]?id|"#,
        r#"cookie|authorization|private[_-]?key)["']?\s*[:=]\s*)(["']?)[^\s"',;&)}\]]{3,}"#,
    ))
});
/// Secret query parameters in URLs (presigned links, OAuth codes, tokens).
static SECRET_QUERY: LazyLock<Regex> = LazyLock::new(|| {
    rule(concat!(
        r#"(?i)([?&](?:token|access_token|refresh_token|id_token|code|key|api_key|apikey|secret|"#,
        r#"signature|sig|password|auth|session|x-amz-signature|x-amz-credential|"#,
        r#"x-amz-security-token)=)[^&\s#"']+"#,
    ))
});
/// `https://user:pass@host`.
static URL_CREDENTIALS: LazyLock<Regex> = LazyLock::new(|| rule(r"(?i)\b([a-z][a-z0-9+.-]*://)[^/\s:@]+:[^/\s@]+@"));
static EMAIL: LazyLock<Regex> = LazyLock::new(|| rule(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"));
/// Other people's and other spellings of user folders: `C:\Users\name`, `/Users/name`,
/// `/home/name`. Python's rule refuses `Public` and `Default` with a look-ahead ([`SHARED`]),
/// and a `/Users/` or `/home/` that follows a word or a `:` (a web address) with a look-behind.
static USER_DIR: LazyLock<Regex> = LazyLock::new(|| rule(r#"(?i)(\b[A-Z]:[\\/]+Users[\\/]+|/Users/|/home/)([^\\/\s"':]+)"#));
static SHARED: LazyLock<Regex> = LazyLock::new(|| rule(r"^(?i)(?:Public\b|Default\b)"));
/// An IPv4 address. Python's rule refuses loopback and unspecified ones with look-aheads
/// ([`NO_ADDRESS`]).
static IPV4: LazyLock<Regex> = LazyLock::new(|| rule(r"\b(?:25[0-5]|2[0-4]\d|1?\d?\d)(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){3}\b"));
static NO_ADDRESS: LazyLock<Regex> = LazyLock::new(|| rule(r"^(?:127\.|0\.0\.0\.0\b)"));

/// The person's home folder in the three spellings logs produce (`\`, `\\` and `/`),
/// longest first, whatever the case.
static HOME: LazyLock<Option<Regex>> = LazyLock::new(|| {
    let home = std::env::var(if cfg!(windows) { "USERPROFILE" } else { "HOME" }).unwrap_or_default();
    let home = home.trim_end_matches(['\\', '/']);
    if home.is_empty() {
        return None;
    }
    let spellings = [home.replace('\\', "\\\\"), home.to_string(), home.replace('\\', "/")];
    let escaped: Vec<String> = spellings.iter().map(|spelling| regex::escape(spelling)).collect();
    Regex::new(&format!("(?i){}", escaped.join("|"))).ok()
});

/// `rule.sub(with, text)` for a rule Python writes with a look-ahead: a match that
/// `refused` turns down is no match, and the search goes on one character later, as
/// Python's does.
fn replace_unless(rule: &Regex, text: &str, refused: impl Fn(&Captures) -> bool, with: impl Fn(&Captures) -> String) -> String {
    let (mut out, mut copied, mut from) = (String::new(), 0, 0);
    while let Some(found) = rule.captures_at(text, from) {
        let all = found.get(0).expect("the whole match");
        if refused(&found) {
            from = all.start() + text[all.start()..].chars().next().map_or(1, char::len_utf8);
            continue;
        }
        out.push_str(&text[copied..all.start()]);
        out.push_str(&with(&found));
        (copied, from) = (all.end(), all.end());
    }
    out.push_str(&text[copied..]);
    out
}

/// `scrub_home`: the person's home folder as `~`.
pub fn scrub_home(text: &str) -> String {
    match &*HOME {
        Some(home) => home.replace_all(text, NoExpand("~")).into_owned(),
        None => text.to_string(),
    }
}

/// `scrub`: `text` without personal data.
pub fn scrub(text: &str) -> String {
    if text.is_empty() {
        return String::new();
    }
    let out = scrub_home(text);
    let out = URL_CREDENTIALS.replace_all(&out, |found: &Captures| format!("{}{REDACTED}@", &found[1]));
    let out = SECRET_QUERY.replace_all(&out, |found: &Captures| format!("{}{REDACTED}", &found[1]));
    let out = TOKENS.replace_all(&out, NoExpand(REDACTED));
    let out = AUTH_SCHEME.replace_all(&out, |found: &Captures| format!("{} {REDACTED}", &found[1]));
    let out = SECRET_FIELD.replace_all(&out, |found: &Captures| format!("{}{}{REDACTED}", &found[1], &found[2]));
    let out = EMAIL.replace_all(&out, NoExpand("[email]"));
    let named = |found: &Captures| {
        let (root, name) = (found.get(1).expect("the root"), found.get(2).expect("the name"));
        let in_an_address = root.as_str().starts_with('/') && out[..root.start()].chars().next_back().is_some_and(|ch| ch.is_alphanumeric() || "_:".contains(ch));
        in_an_address || SHARED.is_match(&out[name.start()..])
    };
    let out = replace_unless(&USER_DIR, &out, named, |found| format!("{}<user>", &found[1]));
    let unlisted = |found: &Captures| NO_ADDRESS.is_match(&out[found.get(0).expect("the whole match").start()..]);
    replace_unless(&IPV4, &out, unlisted, |_| "[ip]".to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn secrets_and_names_go_and_the_line_stays_readable() {
        let cases = [
            ("", ""),
            ("nothing personal here", "nothing personal here"),
            ("open https://bob:hunter2@example.com/x", "open https://[redacted]@example.com/x"),
            ("GET /cb?code=abc123&state=1", "GET /cb?code=[redacted]&state=1"),
            ("key sk-ant-abcdefgh12345678 used", "key [redacted] used"),
            // The scheme's rule, then the field's on what it left.
            ("Authorization: Bearer abcdefgh.12345", "Authorization: [redacted] [redacted]"),
            (r#"{"password": "hunter22"}"#, r#"{"password": "[redacted]"}"#),
            ("mail someone@example.com now", "mail [email] now"),
            (r"C:\Users\Someone\file.txt and C:\Users\Public\x", r"C:\Users\<user>\file.txt and C:\Users\Public\x"),
            ("/home/someone/x /home/default/x /home/defaults/x", "/home/<user>/x /home/default/x /home/<user>/x"),
            ("File \"/Users/pat/a.py\" on example.com/home/page", "File \"/Users/<user>/a.py\" on example.com/home/page"),
            ("from 10.1.2.3 to 127.0.0.1 and 0.0.0.0", "from [ip] to 127.0.0.1 and 0.0.0.0"),
            // Refused at its start, found one octet later, as Python finds it.
            ("at 127.0.0.1.5", "at 127.[ip]"),
        ];
        for (text, scrubbed) in cases {
            assert_eq!(scrub(text), scrubbed, "{text}");
        }
    }
}
