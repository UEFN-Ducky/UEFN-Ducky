//! A slow call never holds up another call, and never hangs its own caller.

use std::net::SocketAddr;
use std::sync::atomic::Ordering;
use std::time::{Duration, Instant};

use axum::routing::get;
use axum::Router;
use backend::guard;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::{TcpListener, TcpStream};
use tokio::task::JoinSet;

const SLOW_FOR: Duration = Duration::from_secs(3);

async fn start(router: Router) -> SocketAddr {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    tokio::spawn(async move { axum::serve(listener, router).await.unwrap() });
    addr
}

/// (status line + headers + body, how long it took).
async fn call(addr: SocketAddr, path: &str) -> (String, Duration) {
    let started = Instant::now();
    let mut s = TcpStream::connect(addr).await.unwrap();
    s.write_all(format!("GET {path} HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n").as_bytes()).await.unwrap();
    let mut out = String::new();
    s.read_to_string(&mut out).await.unwrap();
    (out, started.elapsed())
}

// The one place a thread may sleep: on the blocking pool, as file and database work does.
#[allow(clippy::disallowed_methods)]
async fn blocking_work() -> &'static str {
    tokio::task::spawn_blocking(|| std::thread::sleep(SLOW_FOR)).await.unwrap();
    "done"
}

fn routes() -> Router {
    Router::new()
        .route("/fast", get(|| async { "ok" }))
        .route("/slow", get(|| async { tokio::time::sleep(SLOW_FOR).await; "done" }))
        .route("/blocking", get(blocking_work))
}

// Two workers and 16 slow calls: if a slow call held a worker, the fast ones would queue.
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn fast_calls_answer_while_slow_and_blocking_calls_run() {
    let addr = start(guard::wrap(routes(), guard::CALL_LIMIT)).await;
    let mut slow = JoinSet::new();
    for path in ["/slow", "/blocking"] {
        for _ in 0..8 {
            slow.spawn(call(addr, path));
        }
    }
    tokio::time::sleep(Duration::from_millis(200)).await;

    // 200 calls, 20 at a time: 200 connects in the same instant overflow the listen queue
    // on Windows and the kernel retries them after 500 ms, which is not the server's wait.
    let mut slowest = Duration::ZERO;
    for _ in 0..10 {
        let mut fast = JoinSet::new();
        for _ in 0..20 {
            fast.spawn(call(addr, "/fast"));
        }
        while let Some(done) = fast.join_next().await {
            let (resp, took) = done.unwrap();
            assert!(resp.starts_with("HTTP/1.1 200") && resp.ends_with("ok"), "{resp}");
            slowest = slowest.max(took);
        }
    }
    println!("slowest of 200 fast calls: {slowest:?}");
    // 3-4 ms here; the bound leaves room for a busy CI runner and is still far under the
    // 3 s a fast call would wait if a slow one held a worker.
    assert!(slowest < Duration::from_millis(500), "a fast call took {slowest:?}");
    assert_eq!(slow.len(), 16, "the slow calls are still running");
    while let Some(done) = slow.join_next().await {
        let (resp, took) = done.unwrap();
        assert!(resp.starts_with("HTTP/1.1 200") && resp.ends_with("done"), "{resp}");
        assert!(took >= SLOW_FOR);
    }
}

#[tokio::test]
async fn a_call_past_its_limit_gets_a_json_504_and_is_counted() {
    let addr = start(guard::wrap(routes(), Duration::from_millis(200))).await;
    let before = guard::SLOW_CALLS.load(Ordering::Relaxed);
    let (resp, took) = call(addr, "/slow").await;
    assert!(resp.starts_with("HTTP/1.1 504"), "{resp}");
    assert!(took < Duration::from_millis(1500), "answered after {took:?}");
    let body: serde_json::Value = serde_json::from_str(resp.split("\r\n\r\n").nth(1).unwrap()).unwrap();
    assert_eq!(body, serde_json::json!({"ok": false, "error": "timed out after 200 ms", "path": "/slow"}));
    assert!(guard::SLOW_CALLS.load(Ordering::Relaxed) > before);
    // Under the limit: answered as usual, and not counted as slow.
    let counted = guard::SLOW_CALLS.load(Ordering::Relaxed);
    assert!(call(addr, "/fast").await.0.ends_with("ok"));
    assert_eq!(guard::SLOW_CALLS.load(Ordering::Relaxed), counted);
}
