//! Reaching the update server on a network that gets in the way
//! (`frontend/update_network.py`: `doh_resolve`, `doh_open`; `frontend/version_check.py`:
//! `clock_skew_message`; `frontend/net_access.py`: `diagnose`, `download_leg`).
//!
//! Every probe has its own time limit, so the test answers on a network where nothing does.

use std::net::{IpAddr, SocketAddr};
use std::time::{Duration, Instant};

use reqwest::header::{HeaderMap, HeaderValue};
use reqwest::{Method, Url};
use serde_json::{json, Value};
use tokio::net::TcpStream;
use tokio::time::timeout;

use crate::api::app_info::notes::Failure;
use crate::api::site::{self, proxy, Answer, Refused};

/// DNS-over-HTTPS resolvers addressed by IP (their certificates name the IP), so a PC
/// whose own DNS can't find the Store's host still finds it.
const DOH: [&str; 2] = ["https://1.1.1.1/dns-query", "https://8.8.8.8/resolve"];
const PROBE: Duration = Duration::from_secs(5);
const MONTHS: [&str; 12] = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/// The first IPv4 address in a resolver's JSON answer.
fn doh_address(raw: &str) -> Option<IpAddr> {
    let body: Value = serde_json::from_str(raw).ok()?;
    body["Answer"].as_array()?.iter().filter(|row| row["type"] == 1).find_map(|row| row["data"].as_str()?.parse().ok())
}

/// `doh_resolve`: `host`'s address by a resolver the PC's own DNS has no say in.
pub async fn doh_resolve(host: &str) -> Option<IpAddr> {
    for resolver in DOH {
        let Ok(url) = Url::parse_with_params(resolver, [("name", host), ("type", "A")]) else { continue };
        let mut headers = HeaderMap::new();
        headers.insert("accept", HeaderValue::from_static("application/dns-json"));
        let found = site::send(Method::GET, url, headers, None, PROBE).await.ok().and_then(|answer| doh_address(&answer.raw));
        if found.is_some() {
            return found;
        }
    }
    None
}

/// `doh_open`: the request sent to `address`; the certificate is still checked against
/// the host's name.
pub async fn send_pinned(address: IpAddr, method: Method, url: Url, headers: HeaderMap, body: Option<String>, limit: Duration) -> Result<Answer, Refused> {
    let host = url.host_str().unwrap_or_default().to_string();
    let to = SocketAddr::new(address, url.port_or_known_default().unwrap_or(443));
    let client = site::special("direct".to_string(), move |builder| builder.resolve(&host, to)).await?;
    site::send_on(client, method, url, headers, body, limit).await
}

/// Days since 1970 of a date, and back (the proleptic Gregorian calendar).
fn days_from_civil(year: i64, month: i64, day: i64) -> i64 {
    let year = if month <= 2 { year - 1 } else { year };
    let era = year.div_euclid(400);
    let of_era = year - era * 400;
    let of_year = (153 * (if month > 2 { month - 3 } else { month + 9 }) + 2) / 5 + day - 1;
    era * 146_097 + of_era * 365 + of_era / 4 - of_era / 100 + of_year - 719_468
}

fn civil_from_days(days: i64) -> (i64, i64, i64) {
    let days = days + 719_468;
    let era = days.div_euclid(146_097);
    let of_era = days - era * 146_097;
    let year = (of_era - of_era / 1460 + of_era / 36_524 - of_era / 146_096) / 365;
    let of_year = of_era - (365 * year + year / 4 - year / 100);
    let shifted = (5 * of_year + 2) / 153;
    let month = if shifted < 10 { shifted + 3 } else { shifted - 9 };
    (year + era * 400 + i64::from(month <= 2), month, of_year - (153 * shifted + 2) / 5 + 1)
}

/// `2026-11-28`: the UTC day of a time, as `time.strftime("%Y-%m-%d", time.gmtime())`.
pub fn utc_day(seconds: f64) -> String {
    let (year, month, day) = civil_from_days((seconds as i64).div_euclid(86_400));
    format!("{year:04}-{month:02}-{day:02}")
}

/// The time of a `Date` header (`Tue, 15 Nov 1994 08:12:31 GMT`), in seconds since 1970.
/// ponytail: the one form HTTP servers send; Python's parser also reads the obsolete ones.
fn http_date(text: &str) -> Option<i64> {
    let words: Vec<&str> = text.split_whitespace().collect();
    let [_weekday, day, month, year, clock, "GMT" | "UTC" | "+0000"] = words.as_slice() else { return None };
    let month = MONTHS.iter().position(|name| name.eq_ignore_ascii_case(month))? as i64 + 1;
    let clock: Vec<i64> = clock.split(':').map(str::parse).collect::<Result<_, _>>().ok()?;
    let [hour, minute, second] = clock.as_slice() else { return None };
    Some(days_from_civil(year.parse().ok()?, month, day.parse().ok()?) * 86_400 + hour * 3600 + minute * 60 + second)
}

/// A time on this PC's own clock face: year, month, day, hour, minute.
#[cfg(windows)]
fn local(seconds: i64) -> Option<[i64; 5]> {
    use windows_sys::Win32::Foundation::SYSTEMTIME;
    use windows_sys::Win32::System::Time::SystemTimeToTzSpecificLocalTime;

    let (year, month, day) = civil_from_days(seconds.div_euclid(86_400));
    let of_day = seconds.rem_euclid(86_400);
    let utc = SYSTEMTIME {
        wYear: year as u16,
        wMonth: month as u16,
        wDayOfWeek: 0,
        wDay: day as u16,
        wHour: (of_day / 3600) as u16,
        wMinute: (of_day / 60 % 60) as u16,
        wSecond: (of_day % 60) as u16,
        wMilliseconds: 0,
    };
    // SAFETY: both are whole SYSTEMTIMEs of this frame; a null zone is the PC's own.
    let mut here: SYSTEMTIME = unsafe { std::mem::zeroed() };
    let done = unsafe { SystemTimeToTzSpecificLocalTime(std::ptr::null(), &utc, &mut here) };
    (done != 0).then(|| [here.wYear, here.wMonth, here.wDay, here.wHour, here.wMinute].map(i64::from))
}

#[cfg(not(windows))]
fn local(seconds: i64) -> Option<[i64; 5]> {
    use std::ffi::{c_char, c_int, c_long};

    /// `struct tm` of 64-bit Linux and macOS.
    #[repr(C)]
    struct Tm {
        sec: c_int,
        min: c_int,
        hour: c_int,
        mday: c_int,
        mon: c_int,
        year: c_int,
        wday: c_int,
        yday: c_int,
        isdst: c_int,
        gmtoff: c_long,
        zone: *const c_char,
    }
    extern "C" {
        fn localtime_r(time: *const i64, out: *mut Tm) -> *mut Tm;
    }
    // SAFETY: `out` is a whole `struct tm` of this frame, filled before it is read.
    let mut out: Tm = unsafe { std::mem::zeroed() };
    let done = unsafe { localtime_r(&seconds, &mut out) };
    (!done.is_null()).then(|| [out.year + 1900, out.mon + 1, out.mday, out.hour, out.min].map(i64::from))
}

/// `describe_clock_skew`: a hint when the PC's clock is more than 10 minutes off the
/// server's `Date`.
fn describe_clock_skew(server_date: &str, now: i64) -> Option<String> {
    let server = http_date(server_date).filter(|server| (now - server).abs() >= 600)?;
    let shown = |seconds: i64| local(seconds).map(|[year, month, day, hour, minute]| format!("{day:02} {} {year} {hour:02}:{minute:02}", MONTHS[(month - 1) as usize]));
    let (mine, real) = (shown(now)?, shown(server)?);
    Some(format!(
        "Your PC clock is wrong: it says {mine}, but the real time is {real}. Secure connections fail until Windows date & time is correct (turn on 'Set time automatically')."
    ))
}

/// `clock_skew_message`: only the server's `Date` header is read, over a connection whose
/// certificate is not checked (a wrong clock is why the check fails); the body is never used.
pub async fn clock_skew_message(base: &str) -> Option<String> {
    let url = Url::parse(base).ok()?;
    let mut headers = HeaderMap::new();
    headers.insert("user-agent", HeaderValue::from_str(&site::USER_AGENT).ok()?);
    let client = site::special(site::route_of(&url).await, |builder| builder.danger_accept_invalid_certs(true)).await.ok()?;
    let answer = site::send_on(client, Method::HEAD, url, headers, None, PROBE).await.ok()?;
    describe_clock_skew(answer.headers.get("date")?.to_str().ok()?, crate::store::now() as i64)
}

fn ms(since: Instant) -> u64 {
    (since.elapsed().as_secs_f64() * 1000.0).round() as u64
}

fn cut(text: impl std::fmt::Display, to: usize) -> String {
    text.to_string().chars().take(to).collect()
}

/// A host's addresses, or why there are none.
async fn lookup(host: &str, port: u16) -> Result<Vec<SocketAddr>, String> {
    match timeout(PROBE, tokio::net::lookup_host((host.trim_matches(['[', ']']), port))).await {
        Ok(Ok(found)) => Ok(found.collect()),
        Ok(Err(error)) => Err(error.to_string()),
        Err(_) => Err("timed out".to_string()),
    }
}

async fn connect(to: SocketAddr) -> Result<(), String> {
    match timeout(PROBE, TcpStream::connect(to)).await {
        Ok(Ok(_)) => Ok(()),
        Ok(Err(error)) => Err(cut(error, 120)),
        Err(_) => Err("timed out".to_string()),
    }
}

/// The first address of each family (IPv4, then IPv6).
fn families(found: &[SocketAddr]) -> [(&'static str, Option<SocketAddr>); 2] {
    [("ipv4", found.iter().find(|to| to.is_ipv4()).copied()), ("ipv6", found.iter().find(|to| to.is_ipv6()).copied())]
}

/// `_secure_start`: one connection to `to` and a secure start for `host` on it: ok, or
/// where it died.
/// ponytail: a second connection asks for the host's front page where Python stops after
/// the handshake; any answer means the secure start worked.
async fn secure_start(to: SocketAddr, host: &str) -> Value {
    let started = Instant::now();
    if let Err(error) = connect(to).await {
        return json!({"ok": false, "stage": "connect", "error": error});
    }
    let asked = match Url::parse(&format!("https://{host}:{}/", to.port())) {
        Ok(url) => {
            let name = host.to_string();
            match site::special("direct".to_string(), move |builder| builder.resolve(&name, to)).await {
                Ok(client) => site::send_on(client, Method::HEAD, url, HeaderMap::new(), None, PROBE).await,
                Err(refused) => Err(refused),
            }
        }
        Err(error) => Err(site::network(error)),
    };
    match asked {
        Ok(_) => json!({"ok": true, "ms": ms(started)}),
        Err(refused) => {
            let said = refused.message.trim_start_matches("Network error: ").to_string();
            let closed = ["eof", "reset", "aborted"].iter().any(|word| said.to_lowercase().contains(word));
            json!({"ok": false, "stage": if closed { "cut" } else { "tls" }, "error": cut(said, 120)})
        }
    }
}

/// `download_leg`: where the site's download link leads (its storage host), and whether
/// this PC can start a secure connection to each of that host's address families. The
/// site answering is not enough: plugins it serves itself install on PCs where every
/// update download dies on the storage host.
async fn download_leg(link: &str) -> Value {
    let mut out = json!({"host": "", "ok": false});
    let Ok(url) = Url::parse(link) else { return out };
    let mut headers = HeaderMap::new();
    headers.insert("user-agent", HeaderValue::from_static("UEFN-Ducky"));
    headers.insert("range", HeaderValue::from_static("bytes=0-0"));
    let asked = match site::special(site::route_of(&url).await, |builder| builder.redirect(reqwest::redirect::Policy::none())).await {
        Ok(client) => site::send_on(client, Method::GET, url.clone(), headers, None, PROBE).await,
        Err(refused) => Err(refused),
    };
    let mut location = String::new();
    match asked {
        Ok(answer) => {
            let redirect = [301, 302, 303, 307, 308].contains(&answer.status);
            if redirect || answer.status < 300 {
                location = answer.headers.get("location").and_then(|to| to.to_str().ok()).unwrap_or_default().to_string();
            }
            if answer.status >= 300 && location.is_empty() {
                out["error"] = format!("the site answered {}", answer.status).into();
            }
        }
        Err(refused) => out["error"] = Failure::Network(refused.message).explain(url.host_str().unwrap_or("the site")).into(),
    }
    let target = if location.is_empty() { Some(url) } else { Url::parse(&location).ok() };
    let host = target.as_ref().and_then(Url::host_str).unwrap_or_default().to_string();
    out["host"] = host.trim_matches(['[', ']']).into();
    if !out["error"].is_null() || host.is_empty() {
        return out;
    }
    let found = match lookup(&host, target.and_then(|target| target.port()).unwrap_or(443)).await {
        Ok(found) => found,
        Err(error) => {
            out["error"] = format!("can't look up {host}: {}", cut(error, 100)).into();
            return out;
        }
    };
    for (family, to) in families(&found) {
        out[family] = match to {
            Some(to) => secure_start(to, &host).await,
            None => json!({"ok": null}),
        };
    }
    out["ok"] = (out["ipv4"]["ok"] == true || out["ipv6"]["ok"] == true).into();
    out
}

/// `diagnose`: what a person (or support) needs to see why this PC can't reach `url`.
/// `download` (one of the site's download links) adds where its files come from, and the
/// site's second storage host when it has one. Stays on the PC: nothing is sent anywhere.
pub async fn diagnose(url: &Url, download: &str) -> Value {
    let host = url.host_str().unwrap_or_default().trim_matches(['[', ']']).to_string();
    let scheme = url.scheme().to_string();
    let route = site::route_of(url).await;
    let fixed = tokio::task::spawn_blocking(move || (proxy::mode(), proxy::fixed(&scheme))).await.unwrap_or(("none", String::new()));
    let through = match route.as_str() {
        "direct" => "direct",
        "" if fixed.1.is_empty() => "direct",
        "" => fixed.1.as_str(),
        named => named,
    };
    let mut out = json!({"host": host, "proxy_mode": fixed.0, "proxy": through});
    let started = Instant::now();
    let found = match lookup(&host, url.port_or_known_default().unwrap_or(80)).await {
        Ok(found) => {
            let count = |v4: bool| found.iter().filter(|to| to.is_ipv4() == v4).count();
            out["dns"] = json!({"ok": true, "ms": ms(started), "ipv4": count(true), "ipv6": count(false)});
            found
        }
        Err(error) => {
            out["dns"] = json!({"ok": false, "error": cut(error, 120)});
            Vec::new()
        }
    };
    for (family, to) in families(&found) {
        let started = Instant::now();
        out[family] = match to {
            None => json!({"ok": null}),
            Some(to) => match connect(to).await {
                Ok(()) => json!({"ok": true, "ms": ms(started)}),
                Err(error) => json!({"ok": false, "error": error}),
            },
        };
    }
    let started = Instant::now();
    let mut headers = HeaderMap::new();
    headers.insert("user-agent", HeaderValue::from_static("UEFN-Ducky"));
    // Any answer, an error status too, means the route works.
    out["https"] = match site::send(Method::HEAD, url.clone(), headers, None, PROBE).await {
        Ok(answer) => json!({"ok": true, "status": answer.status, "ms": ms(started)}),
        Err(refused) => {
            let failure = Failure::Network(refused.message);
            json!({"ok": false, "stage": failure.stage(), "error": failure.explain(&host)})
        }
    };
    out["ok"] = out["https"]["ok"].clone();
    if !download.is_empty() && out["ok"] == true {
        out["download"] = download_leg(download).await;
        let other = download_leg(&format!("{download}{}alt=1", if download.contains('?') { "&" } else { "?" })).await;
        if other["host"] != "" && other["host"] != out["download"]["host"] {
            out["download_alt"] = other;
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_resolvers_answer_gives_the_first_ipv4_address() {
        let answer = r#"{"Status": 0, "Answer": [{"type": 5, "data": "edge.example."}, {"type": 1, "data": "not an address"}, {"type": 1, "data": "203.0.113.7"}, {"type": 1, "data": "203.0.113.8"}]}"#;
        assert_eq!(doh_address(answer), Some("203.0.113.7".parse().unwrap()));
        assert!(doh_address(r#"{"Status": 3}"#).is_none() && doh_address("<html>").is_none() && doh_address(r#"{"Answer": "none"}"#).is_none());
    }

    #[test]
    fn a_date_header_is_read_and_a_clock_ten_minutes_off_is_named() {
        assert_eq!(http_date("Thu, 01 Jan 1970 00:00:00 GMT"), Some(0));
        assert_eq!(http_date("Tue, 15 Nov 1994 08:12:31 GMT"), Some(784_887_151));
        assert_eq!(http_date("Sat, 29 Feb 2024 23:59:59 GMT"), Some(1_709_251_199));
        assert!(http_date("").is_none() && http_date("yesterday").is_none() && http_date("Tue, 15 Nov 1994 08:12 GMT").is_none());
        assert_eq!((utc_day(0.0), utc_day(784_887_151.9), utc_day(1_709_251_199.0), utc_day(1_709_251_200.0)), ("1970-01-01".into(), "1994-11-15".into(), "2024-02-29".into(), "2024-03-01".into()));
        for days in [-1, 0, 59, 60, 365, 11_016, 19_782, 20_000] {
            let (year, month, day) = civil_from_days(days);
            assert_eq!(days_from_civil(year, month, day), days);
        }
        let server = "Tue, 15 Nov 1994 08:12:31 GMT";
        assert!(describe_clock_skew(server, 784_887_151 + 599).is_none() && describe_clock_skew("no date", 0).is_none());
        let hint = describe_clock_skew(server, 784_887_151 + 86_400 * 400).unwrap();
        assert!(hint.starts_with("Your PC clock is wrong: it says ") && hint.contains(" 1995 ") && hint.contains(", but the real time is ") && hint.contains(" Nov 1994 "), "{hint}");
    }
}
