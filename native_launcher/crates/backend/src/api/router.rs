//! `POST /__panel_api/<method>`: one panel call, answered in the Python backend's envelope
//! (`panel_httpd.py`, `remote_link/requests.py`, `duckyos_account.call_panel_method`).
//!
//! - body `{"args": [...]}` or `{"args": {...}}` (or the arguments object itself);
//! - 403 for a caller that is not a page on this PC ([`caller_ok`]);
//! - 404 `{"ok": false, "error": "method not allowed"}` for a method that is not here,
//!   403 with the same body for one the caller may not reach;
//! - 200 `{"ok": true, "result": ...}`, or `{"ok": false, "error": ...}` when it refused;
//! - a request id (`X-Ducky-Request-Id`, or `request_id` in the body) runs the call once:
//!   a resend waits for that run or gets its stored answer, with `request_id` and `replayed`.
//!
//! A call is its own task: it finishes even when its request timed out or its caller left,
//! so a resend finds the answer.

use std::collections::{HashMap, VecDeque};
use std::future::Future;
use std::pin::Pin;
use std::sync::{Arc, LazyLock, Mutex, Weak};
use std::time::{Duration, Instant};

use axum::body::Bytes;
use axum::extract::{Path, State};
use axum::http::{HeaderMap, StatusCode, Uri};
use axum::Json;
use serde_json::{json, Map, Value};
use tokio::sync::watch;

use super::push::{Push, POLL_WAIT};
use super::shared::{Outcome, Shared};
use crate::store::http::Repos;

/// A method's run: its arguments by name, as the contract names them.
pub type Answer = Pin<Box<dyn Future<Output = Result<Value, String>> + Send>>;
pub type Run = fn(Arc<Api>, Map<String, Value>) -> Answer;

/// Who may call a method over HTTP (`duckyos_account.remote_denied`).
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Reach {
    /// Any signed-in caller: this PC's windows, a phone, the website.
    Anyone,
    /// A browser on this PC only (Python's `OFF_PC_DENY`).
    ThisPc,
    /// Ducky's own window only, never over HTTP (Python's `REMOTE_DENY`).
    Window,
}

pub struct Method {
    pub run: Run,
    pub reach: Reach,
}

/// How long a finished request's answer is kept for a resend.
const KEEP: Duration = Duration::from_secs(300);
const MAX_KEPT: usize = 4096;
/// How long a resend waits for the run it joined.
const RESEND_WAIT: Duration = Duration::from_secs(60);

/// Everything a method works with.
pub struct Api {
    pub repos: Arc<Repos>,
    pub shared: Arc<Shared>,
    pub push: Push,
    methods: HashMap<&'static str, Method>,
    ledger: Mutex<Ledger>,
    me: Weak<Api>,
}

#[derive(Default)]
struct Ledger {
    rows: HashMap<String, Row>,
    /// Request ids, oldest first.
    order: VecDeque<String>,
}

struct Row {
    answer: watch::Receiver<Option<Outcome>>,
    done_at: Option<Instant>,
}

impl Ledger {
    fn prune(&mut self) {
        while let Some(oldest) = self.order.front() {
            let stale = self.rows.get(oldest).is_none_or(|row| row.done_at.is_some_and(|at| at.elapsed() > KEEP));
            if self.order.len() <= MAX_KEPT && !stale {
                break;
            }
            let oldest = self.order.pop_front().expect("a front row");
            self.rows.remove(&oldest);
        }
    }
}

/// Parameter names per method, with whether each must be given: the contract the page
/// and the Python backend share.
static CONTRACT: LazyLock<HashMap<String, Vec<(String, bool)>>> = LazyLock::new(|| {
    let contract: HashMap<String, Vec<String>> =
        serde_json::from_str(include_str!("../../../../../ducky_app/frontend/ui_web/api_contract.json")).expect("api_contract.json");
    let param = |spec: &String| match spec.split_once('=') {
        Some((name, _default)) => (name.to_string(), false),
        None => (spec.clone(), true),
    };
    // `*args` / `**extra` take nothing by name.
    let params = |specs: Vec<String>| specs.iter().filter(|spec| !spec.starts_with('*')).map(param).collect();
    contract.into_iter().map(|(method, specs)| (method, params(specs))).collect()
});

/// True when the contract has `method`.
pub fn in_contract(method: &str) -> bool {
    CONTRACT.contains_key(method)
}

/// `args` (an object, a positional list, or nothing) by parameter name.
fn named(method: &str, args: Option<&Value>) -> Result<Map<String, Value>, String> {
    let params = CONTRACT.get(method).map(Vec::as_slice).unwrap_or_default();
    let mut out = Map::new();
    match args {
        Some(Value::Object(given)) => {
            for (name, required) in params {
                match given.get(name) {
                    Some(value) => drop(out.insert(name.clone(), value.clone())),
                    None if *required => return Err(format!("missing argument: {name}")),
                    None => {}
                }
            }
        }
        other => {
            let given = other.and_then(Value::as_array).map(Vec::as_slice).unwrap_or_default();
            // ponytail: Python's own TypeError text names the class and counts self; the
            // page shows neither message, it only needs ok: false.
            if given.len() > params.len() {
                return Err(format!("{method}() takes {} positional arguments but {} were given", params.len(), given.len()));
            }
            if let Some((name, _)) = params.iter().skip(given.len()).find(|(_, required)| *required) {
                return Err(format!("{method}() missing 1 required positional argument: '{name}'"));
            }
            out.extend(params.iter().zip(given).map(|((name, _), value)| (name.clone(), value.clone())));
        }
    }
    Ok(out)
}

/// `panel_httpd.host_is_local`: the Host header names this PC.
fn host_is_local(headers: &HeaderMap) -> bool {
    let host = headers.get("host").and_then(|h| h.to_str().ok()).unwrap_or_default();
    let host = host.split('/').next().unwrap_or_default().trim().to_lowercase();
    let name = if host.starts_with('[') {
        format!("{}]", host.split(']').next().unwrap_or_default())
    } else if host.matches(':').count() == 1 {
        host.rsplit_once(':').map(|(name, _port)| name.to_string()).unwrap_or_default()
    } else {
        host
    };
    ["127.0.0.1", "localhost", "::1", "[::1]"].contains(&name.as_str())
}

/// True for a caller this backend may answer: the Host names this PC, and a browser's
/// Origin (when it sends one) is that same host, so no other site's page can call in
/// (`panel_httpd.request_is_authorized`, `webview.panel_post_origin_allowed`).
/// ponytail: a signed-in phone or website (a non-local Host with the remote cookie) is
/// refused here; the launcher sends those to the Python backend, which knows the
/// sessions. Check the cookie here when the remote link moves.
fn caller_ok(headers: &HeaderMap) -> bool {
    let header = |name: &str| headers.get(name).and_then(|h| h.to_str().ok()).map(|h| h.trim().to_lowercase());
    let same_host = |origin: String| origin.split_once("://").is_some_and(|(_, at)| Some(at) == header("host").as_deref());
    host_is_local(headers) && (!headers.contains_key("origin") || header("origin").is_some_and(same_host))
}

impl Reach {
    fn name(self) -> &'static str {
        match self {
            Reach::Anyone => "anyone",
            Reach::ThisPc => "this_pc",
            Reach::Window => "window",
        }
    }
}

/// `GET /__rs/api/methods`: each method here and who may reach it. For the parity tests,
/// which hold it against the Python backend's deny lists.
pub async fn reaches(State(api): State<Arc<Api>>) -> Json<Value> {
    Json(api.methods.iter().map(|(name, method)| (name.to_string(), Value::from(method.reach.name()))).collect::<Map<_, _>>().into())
}

/// `requests.clean_request_id`: the caller's id for this request, or none.
fn request_id(headers: &HeaderMap, payload: &Value) -> Option<String> {
    let header = headers.get("x-ducky-request-id").and_then(|h| h.to_str().ok()).unwrap_or_default();
    let raw = match (header, &payload["request_id"]) {
        ("", Value::String(text)) => text.clone(),
        ("", Value::Number(n)) => n.to_string(),
        (header, _) => header.to_string(),
    };
    let id = raw.trim();
    let ok = (1..=96).contains(&id.len()) && id.bytes().all(|b| b.is_ascii_alphanumeric() || b"._:-".contains(&b));
    ok.then(|| id.to_string())
}

fn refused(status: StatusCode, error: &str) -> (StatusCode, Json<Value>) {
    (status, Json(json!({"ok": false, "error": error})))
}

/// `work` as its own task; a panic in it is an answer too.
async fn finished(work: impl Future<Output = Outcome> + Send + 'static) -> Outcome {
    tokio::spawn(work).await.unwrap_or_else(|_| Err("the call panicked".to_string()))
}

impl Api {
    pub fn new(repos: Arc<Repos>, methods: HashMap<&'static str, Method>) -> Arc<Api> {
        Arc::new_cyclic(|me| Api { repos, shared: Arc::default(), push: Push::default(), methods, ledger: Mutex::default(), me: me.clone() })
    }

    /// This API, for a task that outlives the call starting it.
    pub fn handle(&self) -> Option<Arc<Api>> {
        self.me.upgrade()
    }

    /// Send `events` to the windows, dropping the kept answers each one makes stale.
    pub fn publish(&self, events: Vec<Value>) {
        for event in &events {
            self.shared.on_push(event["type"].as_str().unwrap_or_default());
        }
        self.push.publish(events);
    }

    fn ledger(&self) -> std::sync::MutexGuard<'_, Ledger> {
        self.ledger.lock().unwrap_or_else(|e| e.into_inner())
    }

    /// `work`'s answer, run once per request id; true when this caller did not run it.
    async fn once(self: &Arc<Self>, id: &str, work: impl Future<Output = Outcome> + Send + 'static, wait: Duration) -> (Outcome, bool) {
        let (mut answer, owner) = {
            let mut ledger = self.ledger();
            ledger.prune();
            match ledger.rows.get(id) {
                Some(row) => (row.answer.clone(), false),
                None => {
                    let (done, answer) = watch::channel(None);
                    ledger.rows.insert(id.to_string(), Row { answer: answer.clone(), done_at: None });
                    ledger.order.push_back(id.to_string());
                    let (api, id) = (Arc::clone(self), id.to_string());
                    tokio::spawn(async move {
                        let out = finished(work).await;
                        let mut ledger = api.ledger();
                        match (&out, ledger.rows.get_mut(&id)) {
                            (Ok(_), Some(row)) => row.done_at = Some(Instant::now()),
                            // A failed call may be tried again for real.
                            _ => {
                                ledger.rows.remove(&id);
                                ledger.order.retain(|kept| *kept != id);
                            }
                        }
                        drop(ledger);
                        done.send_replace(Some(out));
                    });
                    (answer, true)
                }
            }
        };
        let answered = async { answer.wait_for(Option::is_some).await.ok().and_then(|out| out.clone()) };
        let out = match tokio::time::timeout(if owner { Duration::MAX } else { wait }, answered).await {
            Ok(out) => out.unwrap_or_else(|| Err("the call ended without an answer".to_string())),
            Err(_) => Err("request still running".to_string()),
        };
        (out, !owner)
    }
}

pub async fn call(
    State(api): State<Arc<Api>>,
    Path(method): Path<String>,
    headers: HeaderMap,
    body: Bytes,
) -> (StatusCode, Json<Value>) {
    if !caller_ok(&headers) {
        return refused(StatusCode::FORBIDDEN, "forbidden");
    }
    let method = method.trim().trim_matches('/').to_string();
    let payload = match body.is_empty() {
        true => Value::Null,
        false => match serde_json::from_slice(&body) {
            Ok(payload) => payload,
            Err(_) => return refused(StatusCode::BAD_REQUEST, "bad request"),
        },
    };
    let Some(found) = api.methods.get(method.as_str()) else {
        return refused(StatusCode::NOT_FOUND, "method not allowed");
    };
    if found.reach == Reach::Window || (found.reach == Reach::ThisPc && !host_is_local(&headers)) {
        return refused(StatusCode::FORBIDDEN, "method not allowed");
    }
    let args = payload.as_object().map(|body| body.get("args").unwrap_or(&payload));
    let id = request_id(&headers, &payload);
    let run = found.run;
    let work = {
        let (api, args) = (Arc::clone(&api), named(&method, args));
        async move {
            let args = args?;
            api.shared.call(&method, &args, || run(Arc::clone(&api), args.clone())).await
        }
    };
    let (out, replayed) = match &id {
        Some(id) => api.once(id, work, RESEND_WAIT).await,
        None => (finished(work).await, false),
    };
    match out {
        Err(error) => refused(StatusCode::OK, &error),
        Ok(result) => {
            // ponytail: a shared answer is copied per caller; answer from the kept bytes
            // if a profile ever shows this copy.
            let mut reply = json!({"ok": true, "result": Arc::unwrap_or_clone(result)});
            if let Some(id) = id {
                reply["request_id"] = id.into();
                reply["replayed"] = replayed.into();
            }
            (StatusCode::OK, Json(reply))
        }
    }
}

/// `GET /__panel_events?since=N`: a window's long-poll.
pub async fn events(State(api): State<Arc<Api>>, headers: HeaderMap, uri: Uri) -> (StatusCode, Json<Value>) {
    if !host_is_local(&headers) {
        return refused(StatusCode::FORBIDDEN, "forbidden");
    }
    let since = uri.query().and_then(|q| q.split('&').find_map(|pair| pair.strip_prefix("since=")));
    let since = since.and_then(|n| n.trim().parse::<i64>().ok()).map_or(0, |n| n.max(0) as u64);
    // head: the newest event when the poll arrived. A page that just loaded replays the
    // backlog up to its first head as history, not live.
    let head = api.push.head();
    let page = api.push.poll(since, POLL_WAIT).await;
    (StatusCode::OK, Json(json!({"cursor": page.cursor, "events": page.events, "head": head})))
}

/// `POST /__panel_event`: another process on this PC (an agent's MCP bridge) publishes
/// one event or a list of them.
pub async fn publish(State(api): State<Arc<Api>>, headers: HeaderMap, body: Bytes) -> StatusCode {
    if !host_is_local(&headers) {
        return StatusCode::FORBIDDEN;
    }
    let events = match serde_json::from_slice(&body) {
        Ok(Value::Array(events)) => events,
        Ok(event) => vec![event],
        Err(_) => return StatusCode::BAD_REQUEST,
    };
    // ponytail: os_notification goes to the tray, which the Python host owns until the
    // host moves; it is not a window event.
    api.publish(events.into_iter().filter(|event| event.is_object() && event["type"] != "os_notification").collect());
    StatusCode::NO_CONTENT
}

#[cfg(test)]
mod tests {
    use std::sync::atomic::{AtomicUsize, Ordering};

    use super::*;

    static RUNS: AtomicUsize = AtomicUsize::new(0);

    /// Answers its arguments and how many runs there were.
    fn echo(_api: Arc<Api>, args: Map<String, Value>) -> Answer {
        Box::pin(async move { Ok(json!({"args": args, "run": RUNS.fetch_add(1, Ordering::SeqCst) + 1})) })
    }

    fn raises(_api: Arc<Api>, _args: Map<String, Value>) -> Answer {
        Box::pin(async { Err("boom".to_string()) })
    }

    static SLOW_RUNS: AtomicUsize = AtomicUsize::new(0);
    static FLAKY_RUNS: AtomicUsize = AtomicUsize::new(0);

    fn slow(_api: Arc<Api>, _args: Map<String, Value>) -> Answer {
        Box::pin(async {
            tokio::time::sleep(Duration::from_millis(150)).await;
            Ok(json!(SLOW_RUNS.fetch_add(1, Ordering::SeqCst) + 1))
        })
    }

    /// Fails the first time.
    fn flaky(_api: Arc<Api>, _args: Map<String, Value>) -> Answer {
        Box::pin(async {
            match FLAKY_RUNS.fetch_add(1, Ordering::SeqCst) {
                0 => Err("not yet".to_string()),
                n => Ok(json!(n + 1)),
            }
        })
    }

    fn api() -> Arc<Api> {
        let method = |run: Run, reach| Method { run, reach };
        let methods = HashMap::from([
            ("agent_send", method(echo, Reach::Anyone)),
            ("save_workflow", method(echo, Reach::Anyone)),
            ("rename_conversation", method(raises, Reach::Anyone)),
            ("open_skill_pack_folder", method(slow, Reach::Anyone)),
            ("delete_conversation", method(flaky, Reach::Anyone)),
            ("list_workflows", method(echo, Reach::Anyone)),
            ("start_verse_lsp", method(echo, Reach::ThisPc)),
            ("pick_project_path", method(echo, Reach::Window)),
        ]);
        assert!(methods.keys().all(|name| in_contract(name)));
        Api::new(Arc::new(Repos::new(std::env::temp_dir())), methods)
    }

    fn headers(pairs: &[(&'static str, &str)]) -> HeaderMap {
        pairs.iter().map(|(name, value)| (axum::http::HeaderName::from_static(name), value.parse().unwrap())).collect()
    }

    /// A call from a page on this PC, unless `headers` name another Host.
    async fn post(api: &Arc<Api>, method: &str, mut headers: HeaderMap, body: Value) -> (u16, Value) {
        if !headers.contains_key("host") {
            headers.insert("host", "127.0.0.1:8765".parse().unwrap());
        }
        let body = if body.is_null() { Bytes::new() } else { Bytes::from(body.to_string()) };
        let (status, Json(reply)) = call(State(Arc::clone(api)), Path(method.to_string()), headers, body).await;
        (status.as_u16(), reply)
    }

    #[tokio::test]
    async fn the_envelope_is_pythons() {
        let api = api();
        let none = HeaderMap::new;
        assert_eq!(post(&api, "no_such_method", none(), json!({"args": []})).await, (404, json!({"ok": false, "error": "method not allowed"})));
        assert_eq!(post(&api, "rename_conversation", none(), json!({"args": ["c", "t"]})).await, (200, json!({"ok": false, "error": "boom"})));
        let (status, reply) = post(&api, "agent_send", none(), json!({"args": ["a", "b", "hi"]})).await;
        assert_eq!((status, &reply["ok"], reply.as_object().unwrap().len()), (200, &json!(true), 2), "{reply}");
        let local = headers(&[("host", "localhost")]);
        let (status, Json(reply)) = call(State(Arc::clone(&api)), Path("agent_send".into()), local, Bytes::from("{not json")).await;
        assert_eq!((status.as_u16(), &reply["ok"]), (400, &json!(false)));
    }

    #[tokio::test]
    async fn arguments_reach_the_method_by_name() {
        let api = api();
        let sent = |body: Value| {
            let api = Arc::clone(&api);
            async move { post(&api, "agent_send", HeaderMap::new(), body).await.1 }
        };
        let by_name = json!({"sender": "a", "to": "b", "message": "hi"});
        assert_eq!(sent(json!({"args": ["a", "b", "hi"]})).await["result"]["args"], by_name);
        assert_eq!(sent(json!({"args": {"message": "hi", "to": "b", "sender": "a", "unknown": 1}})).await["result"]["args"], by_name);
        // No "args": the body is the arguments.
        assert_eq!(sent(by_name.clone()).await["result"]["args"], by_name);
        assert_eq!(sent(json!({"args": ["a", "b", "hi", true]})).await["result"]["args"]["expect_reply"], true);
        assert_eq!(sent(json!({"args": {"sender": "a", "message": "hi"}})).await, json!({"ok": false, "error": "missing argument: to"}));
        assert_eq!(sent(json!({"args": ["a"]})).await["error"], "agent_send() missing 1 required positional argument: 'to'");
        assert_eq!(sent(Value::Null).await["error"], "agent_send() missing 1 required positional argument: 'sender'");
        assert_eq!(sent(json!({"args": [1, 2, 3, 4, 5, 6]})).await["error"], "agent_send() takes 5 positional arguments but 6 were given");
        // `**extra` takes nothing by name, and a method with only defaults needs no body.
        let (_, reply) = post(&api, "save_workflow", HeaderMap::new(), Value::Null).await;
        assert_eq!(reply["result"]["args"], json!({}));
    }

    #[tokio::test]
    async fn a_method_answers_only_the_callers_it_may_reach() {
        let api = api();
        let denied = (403, json!({"ok": false, "error": "method not allowed"}));
        let forbidden = (403, json!({"ok": false, "error": "forbidden"}));
        for host in ["127.0.0.1:8765", "localhost", "[::1]:8765", "LOCALHOST:1"] {
            assert_eq!(post(&api, "start_verse_lsp", headers(&[("host", host)]), Value::Null).await.0, 200, "{host}");
            assert_eq!(post(&api, "pick_project_path", headers(&[("host", host)]), Value::Null).await, denied, "{host}");
        }
        // Not this PC: the Python backend's to answer, it knows who is signed in.
        for host in ["pc.uefnducky.org", "192.168.1.20:8765", "127.0.0.1.evil.org", ""] {
            assert_eq!(post(&api, "agent_send", headers(&[("host", host)]), json!(["a", "b", "c"])).await, forbidden, "{host}");
            let Json(page) = events(State(Arc::clone(&api)), headers(&[("host", host)]), "/__panel_events".parse().unwrap()).await.1;
            assert_eq!(page["ok"], false, "{host}");
        }
        let (status, _) = call(State(Arc::clone(&api)), Path("agent_send".into()), HeaderMap::new(), Bytes::new()).await;
        assert_eq!(status, StatusCode::FORBIDDEN, "no Host");
    }

    #[tokio::test]
    async fn another_sites_page_cannot_call_in() {
        let api = api();
        let from = |origin: &str| headers(&[("host", "127.0.0.1:8765"), ("origin", origin)]);
        for origin in ["http://127.0.0.1:8765", "HTTP://127.0.0.1:8765 "] {
            assert_eq!(post(&api, "agent_send", from(origin), json!(["a", "b", "c"])).await.0, 200, "{origin}");
        }
        for origin in ["null", "", "https://evil.org", "http://127.0.0.1:9999", "http://localhost:8765", "http://127.0.0.1:8765.evil.org"] {
            assert_eq!(post(&api, "agent_send", from(origin), json!(["a", "b", "c"])).await.0, 403, "{origin}");
        }
        let Json(table) = reaches(State(Arc::clone(&api))).await;
        assert_eq!((&table["agent_send"], &table["start_verse_lsp"], &table["pick_project_path"]), (&json!("anyone"), &json!("this_pc"), &json!("window")));
    }

    #[tokio::test]
    async fn a_request_id_runs_the_call_once() {
        let api = api();
        let id = |id: &str| headers(&[("x-ducky-request-id", id)]);
        let args = json!({"args": ["a", "b", "hi"]});
        let (_, first) = post(&api, "agent_send", id("req-1"), args.clone()).await;
        let (_, again) = post(&api, "agent_send", id("req-1"), args.clone()).await;
        assert_eq!((&first["request_id"], &first["replayed"]), (&json!("req-1"), &json!(false)));
        assert_eq!((&again["result"], &again["replayed"]), (&first["result"], &json!(true)));
        // The id may come in the body; another id, a bad id and no id each run for real.
        let in_body = json!({"args": ["a", "b", "hi"], "request_id": "req-1"});
        assert_eq!(post(&api, "agent_send", HeaderMap::new(), in_body).await.1["replayed"], true);
        let (_, other) = post(&api, "agent_send", id("req-2"), args.clone()).await;
        assert_ne!(other["result"]["run"], first["result"]["run"]);
        let (_, bad) = post(&api, "agent_send", id("has space"), args.clone()).await;
        assert_eq!((bad.get("request_id"), bad["result"]["run"] != other["result"]["run"]), (None, true));
        assert_eq!(post(&api, "agent_send", id(&"x".repeat(97)), args).await.1.get("replayed"), None);
    }

    #[tokio::test]
    async fn a_resend_joins_the_run_and_a_failed_call_runs_again() {
        let api = api();
        let id = |id: &str| headers(&[("x-ducky-request-id", id)]);
        let body = json!({"args": ["c"]});
        let (first, resend) =
            tokio::join!(post(&api, "open_skill_pack_folder", id("slow"), body.clone()), post(&api, "open_skill_pack_folder", id("slow"), body.clone()));
        assert_eq!((first.1["result"].clone(), first.1["replayed"].clone()), (json!(1), json!(false)));
        assert_eq!((resend.1["result"].clone(), resend.1["replayed"].clone()), (json!(1), json!(true)));
        assert_eq!(SLOW_RUNS.load(Ordering::SeqCst), 1);

        assert_eq!(post(&api, "delete_conversation", id("flaky"), body.clone()).await.1, json!({"ok": false, "error": "not yet"}));
        let (_, retry) = post(&api, "delete_conversation", id("flaky"), body).await;
        assert_eq!((&retry["result"], &retry["replayed"]), (&json!(2), &json!(false)));
        assert_eq!(api.ledger().order.len(), 2);
    }

    #[tokio::test]
    async fn a_resend_gives_up_on_a_run_that_is_still_going_and_the_run_still_finishes() {
        let api = api();
        let work = || async {
            tokio::time::sleep(Duration::from_millis(120)).await;
            Ok(Arc::new(json!("done")))
        };
        let owner = tokio::spawn({
            let api = Arc::clone(&api);
            async move { api.once("r", work(), RESEND_WAIT).await }
        });
        tokio::time::sleep(Duration::from_millis(20)).await;
        assert_eq!(api.once("r", work(), Duration::from_millis(10)).await, (Err("request still running".to_string()), true));
        // The caller that started it leaves; the run finishes and a resend gets its answer.
        owner.abort();
        assert_eq!(api.once("r", work(), RESEND_WAIT).await, (Ok(Arc::new(json!("done"))), true));
    }

    #[tokio::test]
    async fn the_ledger_forgets_the_oldest_past_its_limit() {
        let api = api();
        for n in 0..MAX_KEPT + 10 {
            api.once(&format!("id-{n}"), async { Ok(Arc::new(Value::Null)) }, RESEND_WAIT).await.0.unwrap();
        }
        let ledger = api.ledger();
        assert!(ledger.order.len() <= MAX_KEPT + 1 && ledger.rows.len() == ledger.order.len(), "{}", ledger.order.len());
        assert!(!ledger.rows.contains_key("id-0") && ledger.rows.contains_key(&format!("id-{}", MAX_KEPT + 9)));
    }

    #[tokio::test]
    async fn windows_poll_events_and_a_published_event_drops_stale_lists() {
        let api = api();
        let local = || headers(&[("host", "127.0.0.1:1")]);
        let (_, first) = post(&api, "list_workflows", local(), Value::Null).await;
        assert_eq!(post(&api, "list_workflows", local(), Value::Null).await.1, first, "kept for a second");
        let event = json!({"type": "project_changed", "root": "x"});
        let body = json!([event, {"type": "os_notification"}, "not an event"]).to_string();
        assert_eq!(publish(State(Arc::clone(&api)), local(), Bytes::from(body)).await, StatusCode::NO_CONTENT);
        assert_ne!(post(&api, "list_workflows", local(), Value::Null).await.1, first);

        let Json(page) = events(State(Arc::clone(&api)), local(), "/__panel_events?x=1&since=0".parse().unwrap()).await.1;
        assert_eq!(page, json!({"cursor": 1, "events": [event], "head": 1}));
        let one = Bytes::from(json!({"type": "chats_changed"}).to_string());
        assert_eq!(publish(State(Arc::clone(&api)), headers(&[("host", "evil.org")]), one.clone()).await, StatusCode::FORBIDDEN);
        assert_eq!(publish(State(Arc::clone(&api)), local(), Bytes::from("nope")).await, StatusCode::BAD_REQUEST);
        assert_eq!(publish(State(Arc::clone(&api)), local(), one).await, StatusCode::NO_CONTENT);
        let Json(page) = events(State(Arc::clone(&api)), local(), "/__panel_events?since=1".parse().unwrap()).await.1;
        assert_eq!(page, json!({"cursor": 2, "events": [{"type": "chats_changed"}], "head": 2}));
    }
}
