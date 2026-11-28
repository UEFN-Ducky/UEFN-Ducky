//! The words an error is reported as (`usage_events.code_of`, `where_of`, `kind_of`): a
//! slug of its source tag, where in the app it came from, and what kind of failure its
//! message reads as. The message itself never leaves the PC.

use std::sync::LazyLock;

use regex::Regex;

/// `code_of`: a slug for an error log source tag.
pub fn code_of(source: &str) -> String {
    let (mut out, mut in_run) = (String::new(), false);
    for ch in source.trim().to_lowercase().chars() {
        let plain = matches!(ch, 'a'..='z' | '0'..='9' | '_' | '.' | '-');
        if plain {
            out.push(ch);
        } else if !in_run {
            out.push('_');
        }
        in_run = !plain;
    }
    let slug = out.trim_matches('_');
    if slug.is_empty() { "unknown" } else { slug }.chars().take(48).collect()
}

/// `where_of`: the part of the app a source tag names.
pub fn where_of(source: &str) -> &'static str {
    const PARTS: [(&str, &[&str]); 7] = [
        ("bridge", &["bridge", "listener", "mcp", "daemon"]),
        ("agent", &["agent", "model", "llm", "stream", "coding"]),
        ("uefn", &["uefn", "verse", "fortnite", "toolset"]),
        ("store", &["store", "plugin", "install"]),
        ("sync", &["sync", "account", "duckyos", "remote"]),
        ("workflow", &["workflow", "automation"]),
        ("ui", &["ui", "panel", "webview", "window", "chat"]),
    ];
    let lower = source.to_lowercase();
    let tokens: Vec<&str> = lower.split(|ch: char| !(ch.is_ascii_lowercase() || ch.is_ascii_digit())).filter(|token| !token.is_empty()).collect();
    let named = |starts: &[&str]| tokens.iter().any(|token| starts.iter().any(|start| token.starts_with(start)));
    PARTS.iter().find(|(_part, starts)| named(starts)).map_or("other", |(part, _starts)| *part)
}

/// First match wins: "timed out waiting for listener idle" is busy, "503 overloaded" a rate limit.
static KINDS: LazyLock<Vec<(&str, Regex)>> = LazyLock::new(|| {
    [
        // WinError 10013 (a port Windows reserved or a firewall holds): first, because its
        // English text says "forbidden" (auth) and its French one "permission".
        ("port_blocked", r"winerror 10013|wsaeacces|\b10013\b"),
        ("session_dead", r"session unavailable|taskgroup|session (is )?closed"),
        ("busy", r"\bbusy\b|waiting for listener idle"),
        ("timeout", r"timed? ?out\b|timeout"),
        ("offline", r"not reachable|offline|connection (refused|reset|aborted)|winerror 100(54|61)"),
        ("rate_limit", r"\b429\b|rate.?limit|overloaded|usage limit"),
        ("auth", r"\b40[13]\b|unauthori[sz]ed|forbidden|not logged in|authentication|invalid api key"),
        // Two 400s told apart from the rest: the chat outgrew the model's window, a picture was refused.
        ("too_long", r"context.?(window|length)|prompt is too long|too many tokens|maximum context|string too long"),
        ("bad_picture", r"\bimage\b.{0,80}(exceed|too (big|large)|invalid|dimension|format|could not|unsupported)|invalid.?image|image_url|image\.source"),
        ("bad_request", r"\b400\b|invalid_request|bad request"),
        ("server_error", r"\b50[0-4]\b|internal server error|bad gateway"),
        ("memory", r"memoryerror|out of memory|\boom\b"),
        ("permission", r"permission denied|access is denied|permissionerror|winerror 5\b"),
        ("not_found", r"not found|no such file|filenotfounderror|does not exist"),
        // A turn that died before its first tool call: Ducky's MCP tools never connected, or
        // the CLI quit. The Store takes only these words, so they read as offline and crash.
        ("offline", r"tools didn.t connect|mcp (server )?(failed|error)|mcp server .{0,40}fail|could not connect to (ducky|mcp)"),
        ("crash", r"exited unsuccessfully|exited with code|process exited"),
        ("crash", r"traceback|crash|exit code|exited with"),
    ]
    .into_iter()
    .map(|(kind, pattern)| (kind, Regex::new(pattern).expect("a fixed pattern")))
    .collect()
});

/// `kind_of`: one word for an error message; only this word is reported.
pub fn kind_of(message: &str) -> &'static str {
    let text = message.to_lowercase();
    KINDS.iter().find(|(_kind, rule)| rule.is_match(&text)).map_or("other", |(kind, _rule)| *kind)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn an_error_is_three_words() {
        assert_eq!((code_of("  UI: Chat  load "), code_of("__"), code_of(&"a".repeat(60)).len()), ("ui_chat_load".to_string(), "unknown".to_string(), 48));
        assert_eq!((code_of("a__b"), code_of("py.ValueError")), ("a__b".to_string(), "py.valueerror".to_string()));
        assert_eq!((where_of("MCP-bridge"), where_of("verse_lsp"), where_of("chat"), where_of("build"), where_of("")), ("bridge", "uefn", "ui", "other", "other"));
        assert_eq!((kind_of("Timed out waiting for listener idle"), kind_of("HTTP 503 overloaded"), kind_of("[WinError 10013] forbidden")), ("busy", "rate_limit", "port_blocked"));
        assert_eq!((kind_of("Traceback (most recent call last)"), kind_of("all fine"), kind_of("")), ("crash", "other", "other"));
    }
}
