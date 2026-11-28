//! Identical callers share one run of a list read, and its answer is kept for a second
//! (`call_pools.py`: `call_shared`, `on_push`).
//!
//! A shared run is its own task: the caller that started it going away (its request timed
//! out, its window closed) never leaves the others waiting.

use std::collections::HashMap;
use std::future::Future;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use serde_json::{Map, Value};
use tokio::sync::watch;

use super::push;
use crate::store::pyjson;

/// Shared by identical callers and kept for [`CACHE_TTL`].
pub const CACHED: &[&str] = &[
    "terminal_list",
    "terminal_busy_many",
    "group_members",
    "list_running_agents",
    "list_all_conversations",
    "get_skill_info",
    "list_project_file_paths",
    "list_coding_agents",
    "list_agent_profiles",
    "list_mcp_servers",
    "list_workflows",
    "has_any_api_key",
];
/// `plugin_data` is cached only for these ops; any other op drops what was cached of it.
const PLUGIN_DATA_READS: &[&str] = &["scope.get", "data.get", "data.list", "files.list"];
/// Shared while one runs, never cached: these must always be fresh.
pub const SINGLE_FLIGHT_ONLY: &[&str] = &["get_settings", "get_project_info", "get_listener_status"];
pub const CACHE_TTL: Duration = Duration::from_secs(1);
pub const CACHE_MAX: usize = 64;
/// Total size kept; one answer over a quarter of it is never cached.
pub const CACHE_MAX_BYTES: usize = 8 * 1024 * 1024;

const CHATS: &[&str] = &["list_all_conversations", "group_members", "list_running_agents"];
const PLUGINS: &[&str] = &["list_mcp_servers", "get_skill_info", "list_coding_agents", "list_agent_profiles", "plugin_data"];
const TERMINALS: &[&str] = &["terminal_list", "terminal_busy_many"];

/// A method's answer, or what it refused with.
pub type Outcome = Result<Arc<Value>, String>;
type Key = (String, String);

struct Kept {
    key: Key,
    until: Instant,
    value: Arc<Value>,
    bytes: usize,
}

#[derive(Default)]
struct Inner {
    flights: HashMap<Key, watch::Receiver<Option<Outcome>>>,
    /// Oldest first; a hit moves to the end.
    cache: Vec<Kept>,
    /// Bumped by every drop: a run that started before one never fills the cache.
    generation: u64,
}

impl Inner {
    fn prune(&mut self) {
        let now = Instant::now();
        self.cache.retain(|kept| kept.until > now);
        let mut total: usize = self.cache.iter().map(|kept| kept.bytes).sum();
        while !self.cache.is_empty() && (self.cache.len() > CACHE_MAX || total > CACHE_MAX_BYTES) {
            total -= self.cache.remove(0).bytes;
        }
    }

    fn keep(&mut self, key: Key, value: Arc<Value>) {
        self.cache.retain(|kept| kept.key != key);
        let bytes = push::size(&value) + key.1.len() + 128;
        if bytes > CACHE_MAX_BYTES / 4 {
            return;
        }
        self.cache.push(Kept { key, until: Instant::now() + CACHE_TTL, value, bytes });
        self.prune();
    }
}

#[derive(Default)]
pub struct Shared {
    inner: Mutex<Inner>,
}

fn cacheable(method: &str, args: &Map<String, Value>) -> bool {
    CACHED.contains(&method)
        || (method == "plugin_data" && args.get("op").and_then(Value::as_str).is_some_and(|op| PLUGIN_DATA_READS.contains(&op)))
}

impl Shared {
    fn inner(&self) -> std::sync::MutexGuard<'_, Inner> {
        self.inner.lock().unwrap_or_else(|e| e.into_inner())
    }

    /// `run`'s answer for `method(args)`. List reads and the settings/project/listener
    /// getters share one run between identical callers.
    pub async fn call<Fut>(self: &Arc<Self>, method: &str, args: &Map<String, Value>, run: impl FnOnce() -> Fut) -> Outcome
    where
        Fut: Future<Output = Result<Value, String>> + Send + 'static,
    {
        let cache = cacheable(method, args);
        if !cache && !SINGLE_FLIGHT_ONLY.contains(&method) {
            let out = run().await.map(Arc::new);
            if method == "plugin_data" {
                // A write: the next read sees it, not a copy kept from before.
                self.drop_cached(Some(&["plugin_data"]));
            }
            return out;
        }
        let key: Key = (method.to_string(), pyjson::sorted(&Value::Object(args.clone())).to_string());
        let mut flight = {
            let mut inner = self.inner();
            if cache {
                inner.prune();
                if let Some(at) = inner.cache.iter().position(|kept| kept.key == key) {
                    let hit = inner.cache.remove(at);
                    let value = Arc::clone(&hit.value);
                    inner.cache.push(hit);
                    return Ok(value);
                }
            }
            match inner.flights.get(&key) {
                Some(flight) => flight.clone(),
                None => {
                    let (done, flight) = watch::channel(None);
                    inner.flights.insert(key.clone(), flight.clone());
                    let (shared, generation, running) = (Arc::clone(self), inner.generation, run());
                    tokio::spawn(async move {
                        // Its own task again: a panic in the method is an answer too, and
                        // the flight always leaves the table.
                        let out = match tokio::spawn(running).await {
                            Ok(out) => out.map(Arc::new),
                            Err(_) => Err("the call panicked".to_string()),
                        };
                        let mut inner = shared.inner();
                        inner.flights.remove(&key);
                        if let (true, Ok(value), true) = (cache, &out, inner.generation == generation) {
                            inner.keep(key, Arc::clone(value));
                        }
                        drop(inner);
                        done.send_replace(Some(out));
                    });
                    flight
                }
            }
        };
        let answered = flight.wait_for(Option::is_some).await;
        match answered {
            Ok(out) => out.clone().expect("waited for an answer"),
            Err(_) => Err("the call ended without an answer".to_string()),
        }
    }

    /// Drop the kept answers a pushed event makes stale.
    pub fn on_push(&self, kind: &str) {
        if kind.ends_with("_settings_changed") || ["settings_changed", "project_changed", "project_switched"].contains(&kind) {
            self.drop_cached(None);
        } else if kind == "chats_changed" || kind.starts_with("group") {
            self.drop_cached(Some(CHATS));
        } else if kind == "uefn_plugins_changed" || kind == "gateway_accounts_changed" {
            self.drop_cached(Some(PLUGINS));
        } else if kind.starts_with("terminal") {
            self.drop_cached(Some(TERMINALS));
        }
    }

    fn drop_cached(&self, methods: Option<&[&str]>) {
        let mut inner = self.inner();
        inner.generation += 1;
        inner.cache.retain(|kept| methods.is_some_and(|names| !names.contains(&kept.key.0.as_str())));
    }
}

#[cfg(test)]
mod tests {
    use std::sync::atomic::{AtomicUsize, Ordering};

    use serde_json::json;

    use super::*;

    fn args(value: Value) -> Map<String, Value> {
        value.as_object().cloned().unwrap_or_default()
    }

    /// A method that counts its runs and answers after `ms`.
    fn counted(runs: &Arc<AtomicUsize>, ms: u64) -> impl FnOnce() -> std::pin::Pin<Box<dyn Future<Output = Result<Value, String>> + Send>> {
        let runs = Arc::clone(runs);
        move || {
            Box::pin(async move {
                let run = runs.fetch_add(1, Ordering::SeqCst) + 1;
                tokio::time::sleep(Duration::from_millis(ms)).await;
                Ok(json!(run))
            })
        }
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 4)]
    async fn fifty_callers_at_once_run_a_shared_method_once() {
        for method in ["get_settings", "list_all_conversations"] {
            let (shared, runs) = (Arc::new(Shared::default()), Arc::new(AtomicUsize::new(0)));
            let callers: Vec<_> = (0..50)
                .map(|_| {
                    let (shared, runs) = (Arc::clone(&shared), Arc::clone(&runs));
                    tokio::spawn(async move { shared.call(method, &args(json!({"a": 1, "b": 2})), counted(&runs, 200)).await })
                })
                .collect();
            for caller in callers {
                assert_eq!(caller.await.unwrap(), Ok(Arc::new(json!(1))), "{method}");
            }
            assert_eq!(runs.load(Ordering::SeqCst), 1, "{method}");
        }
    }

    #[tokio::test]
    async fn a_list_read_is_kept_for_a_second_and_a_getter_never() {
        let (shared, runs) = (Arc::new(Shared::default()), Arc::new(AtomicUsize::new(0)));
        let call = |method: &'static str, a: Value| {
            let (shared, runs) = (Arc::clone(&shared), Arc::clone(&runs));
            async move { shared.call(method, &args(a), counted(&runs, 0)).await.unwrap() }
        };
        assert_eq!(*call("list_workflows", json!({"owner": "a", "q": ""})).await, json!(1));
        // The same arguments in another order are the same call; other arguments are not.
        assert_eq!(*call("list_workflows", json!({"q": "", "owner": "a"})).await, json!(1));
        assert_eq!(*call("list_workflows", json!({"owner": "b"})).await, json!(2));
        assert_eq!(*call("get_settings", json!({})).await, json!(3));
        assert_eq!(*call("get_settings", json!({})).await, json!(4));
        assert_eq!(*call("save_settings", json!({})).await, json!(5));
        assert_eq!(*call("save_settings", json!({})).await, json!(6));
        tokio::time::sleep(CACHE_TTL + Duration::from_millis(50)).await;
        assert_eq!(*call("list_workflows", json!({"owner": "a", "q": ""})).await, json!(7));
    }

    #[tokio::test]
    async fn a_pushed_event_drops_the_lists_it_makes_stale() {
        let (shared, runs) = (Arc::new(Shared::default()), Arc::new(AtomicUsize::new(0)));
        let call = |method: &'static str, a: Value| {
            let (shared, runs) = (Arc::clone(&shared), Arc::clone(&runs));
            async move { shared.call(method, &args(a), counted(&runs, 0)).await.unwrap() }
        };
        let read = json!({"plugin_id": "p", "op": "data.get"});
        assert_eq!(*call("list_all_conversations", json!({})).await, json!(1));
        assert_eq!(*call("terminal_list", json!({})).await, json!(2));
        assert_eq!(*call("plugin_data", read.clone()).await, json!(3));
        shared.on_push("terminal_output");
        shared.on_push("agent_event");
        assert_eq!(*call("list_all_conversations", json!({})).await, json!(1));
        assert_eq!(*call("terminal_list", json!({})).await, json!(4));
        shared.on_push("group_changed");
        assert_eq!(*call("list_all_conversations", json!({})).await, json!(5));
        assert_eq!(*call("plugin_data", read.clone()).await, json!(3));
        // A plugin_data write drops its reads; so does a plugin change.
        assert_eq!(*call("plugin_data", json!({"plugin_id": "p", "op": "data.set"})).await, json!(6));
        assert_eq!(*call("plugin_data", read.clone()).await, json!(7));
        shared.on_push("uefn_plugins_changed");
        assert_eq!(*call("plugin_data", read).await, json!(8));
        shared.on_push("appearance_settings_changed");
        assert_eq!(*call("terminal_list", json!({})).await, json!(9));
    }

    #[tokio::test]
    async fn a_run_that_started_before_a_drop_is_not_kept_and_an_error_is_shared_not_kept() {
        let (shared, runs) = (Arc::new(Shared::default()), Arc::new(AtomicUsize::new(0)));
        let slow = tokio::spawn({
            let (shared, runs) = (Arc::clone(&shared), Arc::clone(&runs));
            async move { shared.call("terminal_list", &Map::new(), counted(&runs, 100)).await }
        });
        tokio::time::sleep(Duration::from_millis(30)).await;
        shared.on_push("terminal_closed");
        assert_eq!(slow.await.unwrap(), Ok(Arc::new(json!(1))));
        assert_eq!(shared.call("terminal_list", &Map::new(), counted(&runs, 0)).await, Ok(Arc::new(json!(2))));

        let failing = || async {
            tokio::time::sleep(Duration::from_millis(50)).await;
            Err::<Value, String>("no".into())
        };
        let none = Map::new();
        let (first, second) = tokio::join!(
            shared.call("list_mcp_servers", &none, failing),
            shared.call("list_mcp_servers", &none, || async { Ok(json!("second ran")) }),
        );
        assert_eq!((first, second), (Err("no".to_string()), Err("no".to_string())));
        assert_eq!(shared.call("list_mcp_servers", &Map::new(), counted(&runs, 0)).await, Ok(Arc::new(json!(3))));
    }

    #[tokio::test]
    async fn the_caller_that_started_a_run_going_away_leaves_the_others_their_answer() {
        let (shared, runs) = (Arc::new(Shared::default()), Arc::new(AtomicUsize::new(0)));
        let first = tokio::spawn({
            let (shared, runs) = (Arc::clone(&shared), Arc::clone(&runs));
            async move { shared.call("get_settings", &Map::new(), counted(&runs, 150)).await }
        });
        tokio::time::sleep(Duration::from_millis(30)).await;
        first.abort();
        assert_eq!(shared.call("get_settings", &Map::new(), counted(&runs, 0)).await, Ok(Arc::new(json!(1))));
        // A method that panics answers too, and its flight is gone afterwards.
        let panics = || async { panic!("boom") };
        assert_eq!(shared.call("get_settings", &Map::new(), panics).await, Err("the call panicked".to_string()));
        assert_eq!(shared.call("get_settings", &Map::new(), counted(&runs, 0)).await, Ok(Arc::new(json!(2))));
    }

    #[tokio::test]
    async fn the_cache_holds_64_answers_and_none_over_a_quarter_of_its_size() {
        let (shared, runs) = (Arc::new(Shared::default()), Arc::new(AtomicUsize::new(0)));
        for n in 0..CACHE_MAX + 6 {
            shared.call("get_skill_info", &args(json!({"name": n})), counted(&runs, 0)).await.unwrap();
        }
        assert_eq!(shared.inner().cache.len(), CACHE_MAX);
        // The oldest left; the newest is still kept.
        assert_eq!(*shared.call("get_skill_info", &args(json!({"name": 0})), counted(&runs, 0)).await.unwrap(), json!(71));
        assert_eq!(*shared.call("get_skill_info", &args(json!({"name": 69})), counted(&runs, 0)).await.unwrap(), json!(70));
        let big = || async { Ok(json!("x".repeat(CACHE_MAX_BYTES / 4))) };
        shared.call("list_workflows", &Map::new(), big).await.unwrap();
        assert!(shared.inner().cache.iter().all(|kept| kept.key.0 != "list_workflows"));
    }
}
