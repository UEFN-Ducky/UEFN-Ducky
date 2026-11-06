use splash::{first_paint, FirstPaint};

#[test]
fn decision_table() {
    // (cache_present, backend_ready) -> decision
    let table = [
        (true, false, FirstPaint::Navigate),   // paint from cache, proxy holds API calls
        (true, true, FirstPaint::Navigate),    // nothing to wait for
        (false, true, FirstPaint::Navigate),   // backend serves the page
        (false, false, FirstPaint::KeepSplash), // nothing to paint yet
    ];
    for (cache, ready, want) in table {
        assert_eq!(first_paint(cache, ready), want, "cache={cache} ready={ready}");
    }
}
