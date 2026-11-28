//! Events for the windows: `GET /__panel_events?since=N` waits up to [`POLL_WAIT`] for
//! events after `N` and answers `{"cursor", "events", "head"}`, as the Python backend does
//! (`panel_httpd.py`, `event_backlog.py`).
//!
//! Every event gets the next number under one lock, so a window reads them in the order
//! they were published: one chat's events never overtake each other.

use std::collections::VecDeque;
use std::sync::Mutex;
use std::time::Duration;

use serde_json::{json, Value};
use tokio::sync::watch;

pub const MAX_EVENTS: usize = 4000;
/// Tool events carry whole file texts: the backlog is capped by size too.
pub const MAX_BYTES: usize = 4 * 1024 * 1024;
/// Events one poll answers with.
pub const PAGE: usize = 500;
/// How long a poll waits for a first event.
pub const POLL_WAIT: Duration = Duration::from_secs(20);

/// About how many bytes `value` takes as JSON, without serializing it.
pub fn size(value: &Value) -> usize {
    match value {
        Value::String(text) => text.chars().count() + 2,
        Value::Object(map) => 2 + map.iter().map(|(key, item)| key.chars().count() + 4 + size(item)).sum::<usize>(),
        Value::Array(items) => 2 + items.len() + items.iter().map(size).sum::<usize>(),
        _ => 24,
    }
}

struct Backlog {
    rows: VecDeque<(u64, Value, usize)>,
    bytes: usize,
    /// The newest event dropped to make room: a window that last read before it missed events.
    dropped_through: u64,
}

pub struct Push {
    backlog: Mutex<Backlog>,
    /// The newest event's number.
    seq: watch::Sender<u64>,
    max_events: usize,
    max_bytes: usize,
}

/// What one poll answers.
pub struct Page {
    pub cursor: u64,
    pub events: Vec<Value>,
}

impl Default for Push {
    fn default() -> Push {
        Push::new(MAX_EVENTS, MAX_BYTES)
    }
}

impl Push {
    pub fn new(max_events: usize, max_bytes: usize) -> Push {
        let backlog = Backlog { rows: VecDeque::new(), bytes: 0, dropped_through: 0 };
        Push { backlog: Mutex::new(backlog), seq: watch::channel(0).0, max_events, max_bytes }
    }

    /// The newest event's number.
    pub fn head(&self) -> u64 {
        *self.seq.borrow()
    }

    /// Add `events` in order; waiting polls wake.
    pub fn publish(&self, events: impl IntoIterator<Item = Value>) {
        let mut backlog = self.backlog.lock().unwrap_or_else(|e| e.into_inner());
        let mut seq = self.head();
        for event in events {
            seq += 1;
            let bytes = size(&event);
            backlog.rows.push_back((seq, event, bytes));
            backlog.bytes += bytes;
            // The newest event always stays, however big, so pollers still receive it.
            while backlog.rows.len() > 1 && (backlog.rows.len() > self.max_events || backlog.bytes > self.max_bytes) {
                let (dropped, _, dropped_bytes) = backlog.rows.pop_front().expect("more than one row");
                backlog.bytes -= dropped_bytes;
                backlog.dropped_through = backlog.dropped_through.max(dropped);
            }
        }
        // Sent under the lock: numbers reach the pollers in the order they were given.
        self.seq.send_replace(seq);
    }

    /// The events after `since`, waiting up to `wait` for the first one.
    pub async fn poll(&self, since: u64, wait: Duration) -> Page {
        let mut seq = self.seq.subscribe();
        let _ = tokio::time::timeout(wait, seq.wait_for(|head| *head > since)).await;
        let backlog = self.backlog.lock().unwrap_or_else(|e| e.into_inner());
        let rows: Vec<&(u64, Value, usize)> = backlog.rows.iter().filter(|row| row.0 > since).take(PAGE).collect();
        let Some(last) = rows.last() else {
            return Page { cursor: self.head(), events: Vec::new() };
        };
        let mut events = Vec::with_capacity(rows.len() + 1);
        // It read up to `since`, and events after that were dropped before it got them:
        // the page reloads what it shows.
        if 0 < since && since < backlog.dropped_through {
            events.push(json!({"type": "remote_resync", "reason": "events_missed"}));
        }
        // A window that has read nothing yet must not replay an old kick.
        events.extend(rows.iter().filter(|row| since > 0 || row.1["type"] != "remote_gone").map(|row| row.1.clone()));
        Page { cursor: last.0, events }
    }
}

#[cfg(test)]
mod tests {
    use std::sync::Arc;

    use super::*;

    const NOW: Duration = Duration::from_millis(0);

    #[tokio::test(flavor = "multi_thread", worker_threads = 4)]
    async fn each_chats_events_arrive_in_send_order_under_load() {
        let push = Arc::new(Push::default());
        let senders: Vec<_> = (0..8)
            .map(|chat| {
                let push = Arc::clone(&push);
                tokio::spawn(async move {
                    for n in 0..400 {
                        push.publish([json!({"type": "agent_event", "conv_id": chat, "n": n})]);
                        if n % 50 == 0 {
                            tokio::task::yield_now().await;
                        }
                    }
                })
            })
            .collect();
        let (mut since, mut seen) = (0, vec![0; 8]);
        while seen.iter().sum::<u64>() < 8 * 400 {
            let page = push.poll(since, Duration::from_secs(10)).await;
            assert!(page.cursor > since, "no event in 10 s after {since}");
            for event in &page.events {
                let chat = event["conv_id"].as_u64().unwrap() as usize;
                assert_eq!(event["n"], seen[chat], "chat {chat} out of order");
                seen[chat] += 1;
            }
            since = page.cursor;
        }
        for sender in senders {
            sender.await.unwrap();
        }
        assert_eq!(push.head(), 8 * 400);
    }

    #[tokio::test]
    async fn a_poll_waits_for_the_next_event_and_gives_up_at_its_limit() {
        let push = Arc::new(Push::default());
        let empty = push.poll(0, Duration::from_millis(20)).await;
        assert_eq!((empty.cursor, empty.events.len()), (0, 0));
        let waiting = tokio::spawn({
            let push = Arc::clone(&push);
            async move { push.poll(0, Duration::from_secs(10)).await }
        });
        tokio::time::sleep(Duration::from_millis(20)).await;
        push.publish([json!({"type": "chats_changed"})]);
        let page = waiting.await.unwrap();
        assert_eq!((page.cursor, page.events), (1, vec![json!({"type": "chats_changed"})]));
    }

    #[tokio::test]
    async fn a_window_that_fell_behind_is_told_to_reload() {
        let push = Push::new(3, MAX_BYTES);
        push.publish((1..=5).map(|n| json!({"n": n})));
        // Events 1 and 2 are gone: a window that read up to 1 missed 2.
        let page = push.poll(1, NOW).await;
        assert_eq!(page.cursor, 5);
        assert_eq!(page.events[0], json!({"type": "remote_resync", "reason": "events_missed"}));
        assert_eq!(page.events[1..], [json!({"n": 3}), json!({"n": 4}), json!({"n": 5})]);
        // A window that has read nothing loads its state itself, and one that is level missed nothing.
        assert_eq!(push.poll(0, NOW).await.events.len(), 3);
        assert_eq!(push.poll(2, NOW).await.events.len(), 3);
        assert_eq!(push.poll(5, NOW).await.events.len(), 0);
    }

    #[tokio::test]
    async fn the_backlog_is_capped_by_size_and_keeps_the_newest_event() {
        let push = Push::new(MAX_EVENTS, 100);
        push.publish([json!({"text": "a".repeat(80)}), json!({"text": "b".repeat(500)})]);
        let page = push.poll(0, NOW).await;
        assert_eq!((page.cursor, page.events.len()), (2, 1));
        // Python's event_size: {"k": "ab", "n": [1, 2]} = 2 + (1+4) + 4 + (1+4) + (2+2) + 48.
        assert_eq!(size(&json!({"k": "ab", "n": [1, 2]})), 68);
    }

    #[tokio::test]
    async fn an_old_kick_is_not_replayed_to_a_new_window_and_a_page_holds_500() {
        let push = Push::default();
        push.publish([json!({"type": "remote_gone"})]);
        push.publish((0..600).map(|n| json!({"n": n})));
        let first = push.poll(0, NOW).await;
        assert_eq!((first.cursor, first.events.len()), (500, 499));
        assert_eq!(push.poll(first.cursor, NOW).await.events.len(), 101);
        push.publish([json!({"type": "remote_gone"})]);
        assert_eq!(push.poll(601, NOW).await.events, [json!({"type": "remote_gone"})]);
    }
}
