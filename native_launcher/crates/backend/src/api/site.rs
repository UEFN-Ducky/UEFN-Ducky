//! Calls to the account's website (`frontend/duckyos_account.py`: `api_request`,
//! `_plugin_collect`, `_site_failure`), without a thread held while the site answers.
//!
//! The PC's network is followed as the Python backend follows it (`frontend/net_access.py`):
//! certificates are checked against the OS store, a proxy auto-config script is asked per
//! host, and a host with a dead IPv6 route is reached over IPv4.
//! ponytail: an address that cuts the secure handshake is not skipped for the host's next
//! one (`net_access.FallbackHTTPSConnection`); that guards the update downloads' storage
//! host and moves here with them.

pub(super) mod proxy;

use std::collections::HashMap;
use std::sync::{LazyLock, Mutex};
use std::time::{Duration, Instant};

use reqwest::header::{HeaderMap, HeaderValue};
use reqwest::{Client, ClientBuilder, Url};
use serde_json::{Map, Value};

use super::settings::{failed, load};
use super::Api;
use crate::store::account;
use crate::store::pyjson::{dumps, pystr, truthy};

pub const DEFAULT_BASE_URL: &str = "https://uefnducky.org";
/// How long an auto-config answer is kept per host, and how long a lookup may take
/// before the call goes without it (a broken network can hang WPAD).
const PAC_CACHE: Duration = Duration::from_secs(300);
const PAC_WAIT: Duration = Duration::from_secs(3);

/// `UEFN-Ducky/<the app's version>`.
pub static USER_AGENT: LazyLock<String> = LazyLock::new(|| {
    let source = include_str!("../../../../../ducky_app/frontend/__init__.py");
    let version = source.split("__version__ = \"").nth(1).and_then(|rest| rest.split('"').next());
    format!("UEFN-Ducky/{}", version.expect("frontend/__init__.py names __version__"))
});

/// The running app's version (`frontend.__version__`).
pub fn app_version() -> &'static str {
    USER_AGENT.split('/').nth(1).unwrap_or_default()
}

/// Why a call has no answer to use (`DuckyOSAccountError`): a sentence for the person
/// and a code for the page.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Refused {
    pub message: String,
    pub code: String,
}

impl Refused {
    pub fn new(message: impl Into<String>, code: &str) -> Refused {
        Refused { message: message.into(), code: code.to_string() }
    }
}

/// The site's answer: its status, its body, and the body as an object when it is one.
pub struct Answer {
    pub status: u16,
    pub parsed: Option<Map<String, Value>>,
    pub raw: String,
    pub headers: HeaderMap,
}

/// One request (`api_request`'s arguments).
pub struct Call<'a> {
    pub method: &'a str,
    pub path: &'a str,
    pub body: Option<&'a Value>,
    /// With no login, ask as nobody instead of refusing.
    pub allow_anonymous: bool,
    /// Never as the account, signed in or not: no key, no cookie, and no answer changes
    /// the login (usage stats).
    pub anonymous: bool,
    /// The longest wait for the site's next bytes.
    pub timeout: Duration,
}

/// `str(value or "")`.
pub(super) fn text(value: &Value) -> String {
    if truthy(value) {
        pystr(value)
    } else {
        String::new()
    }
}

/// `urlparse`'s scheme and host part of an address that has `://`.
fn scheme_and_host(url: &str) -> (String, &str) {
    let (scheme, rest) = url.split_once("://").unwrap_or(("", url));
    (scheme.to_lowercase(), rest.split(['/', '?', '#']).next().unwrap_or_default())
}

/// `normalize_base_url`: `https://<host>`, the site's own address for none.
pub fn normalize_base_url(raw: &str) -> Result<String, Refused> {
    let text = raw.trim().trim_end_matches('/');
    if text.is_empty() {
        return Ok(DEFAULT_BASE_URL.to_string());
    }
    let text = if text.contains("://") { text.to_string() } else { format!("https://{text}") };
    let (scheme, host) = scheme_and_host(&text);
    if scheme != "https" {
        return Err(Refused::new("Account URL must use https://", "invalid_url"));
    }
    if host.is_empty() {
        return Err(Refused::new("Account URL is missing a host", "invalid_url"));
    }
    Ok(format!("https://{host}"))
}

/// `resolve_base_url()`: `DUCKYOS_BASE_URL`, then the saved setting, then the site.
pub async fn resolve_base_url(api: &Api) -> Result<String, Refused> {
    if let Some(env) = std::env::var("DUCKYOS_BASE_URL").ok().filter(|url| !url.trim().is_empty()) {
        return normalize_base_url(&env);
    }
    let saved = load(api).await.map(|settings| text(settings.get("duckyos_base_url"))).unwrap_or_default();
    match normalize_base_url(&saved) {
        Ok(base) if !saved.trim().is_empty() => {
            let (_, host) = scheme_and_host(&base);
            let name = host.rsplit('@').next().unwrap_or_default().split(':').next().unwrap_or_default();
            // The old platform subdomain: always the product's own host.
            Ok(if name.eq_ignore_ascii_case("uefn-ducky.duckyos.org") { DEFAULT_BASE_URL.to_string() } else { base })
        }
        _ => Ok(DEFAULT_BASE_URL.to_string()),
    }
}

pub(super) fn network(error: impl std::fmt::Display) -> Refused {
    Refused::new(format!("Network error: {error}"), "network")
}

fn timed_out() -> Refused {
    network("timed out")
}

/// A failed send or read, with what caused it (the client's own words stop at "error
/// sending request").
fn broken(error: reqwest::Error) -> Refused {
    if error.is_timeout() {
        return timed_out();
    }
    // The address is the caller's own: without it the cause fits where the words are cut short.
    let error = error.without_url();
    let mut said = error.to_string();
    let mut cause = std::error::Error::source(&error);
    while let Some(inner) = cause {
        said = format!("{said}: {inner}");
        cause = inner.source();
    }
    network(said)
}

/// The proxy `url` takes by the PC's auto-config (`PacProxyHandler.proxy_for`): empty
/// for the client's default route (Internet Options' fixed proxy, or the environment's).
async fn auto_proxy(url: &Url) -> String {
    /// Per `scheme://host`: when it was asked, and the answer.
    static CACHE: LazyLock<Mutex<HashMap<String, (Instant, String)>>> = LazyLock::new(Mutex::default);
    let scheme = url.scheme().to_string();
    let key = format!("{scheme}://{}", url.host_str().unwrap_or_default().to_lowercase());
    let kept = CACHE.lock().unwrap_or_else(|e| e.into_inner()).get(&key).filter(|(at, _)| at.elapsed() < PAC_CACHE).map(|(_, proxy)| proxy.clone());
    if let Some(proxy) = kept {
        return proxy;
    }
    let asked = url.to_string();
    let lookup = tokio::task::spawn_blocking(move || proxy::auto_config_answer(&asked));
    let answer = tokio::time::timeout(PAC_WAIT, lookup).await.ok().and_then(Result::ok).unwrap_or_default();
    let proxy = proxy::pick(&answer, &scheme);
    CACHE.lock().unwrap_or_else(|e| e.into_inner()).insert(key, (Instant::now(), proxy.clone()));
    proxy
}

/// The route `url` takes: `direct` on this PC, a proxy its auto-config names, or empty
/// for the client's default (Internet Options' fixed proxy, or the environment's).
pub(super) async fn route_of(url: &Url) -> String {
    let host = url.host_str().unwrap_or_default().trim_matches(['[', ']']).to_lowercase();
    if ["localhost", "127.0.0.1", "::1"].contains(&host.as_str()) || host.starts_with("127.") {
        return "direct".to_string();
    }
    auto_proxy(url).await
}

/// A client of its own for one job (no redirects, one fixed address, no certificate
/// check): `route`'s client with `tweak` applied. Never kept.
pub(super) async fn special(route: String, tweak: impl FnOnce(ClientBuilder) -> ClientBuilder + Send + 'static) -> Result<Client, Refused> {
    let build = move || {
        let builder = tweak(Client::builder());
        match route.as_str() {
            "" => builder.build(),
            "direct" => builder.no_proxy().build(),
            proxy => builder.proxy(reqwest::Proxy::all(proxy)?).build(),
        }
    };
    // Off the async workers: a new client reads the OS certificate store.
    tokio::task::spawn_blocking(build).await.map_err(network)?.map_err(network)
}

/// The client for one route: `direct`, the default (empty) or a named proxy. Each keeps
/// its connections open between calls.
async fn client(route: String) -> Result<Client, Refused> {
    static CLIENTS: LazyLock<Mutex<HashMap<String, Client>>> = LazyLock::new(Mutex::default);
    if let Some(client) = CLIENTS.lock().unwrap_or_else(|e| e.into_inner()).get(&route) {
        return Ok(client.clone());
    }
    let client = special(route.clone(), |builder| builder).await?;
    Ok(CLIENTS.lock().unwrap_or_else(|e| e.into_inner()).entry(route).or_insert(client).clone())
}

/// The body as an object, when it is one (`api_request`'s `parsed`).
fn parsed(raw: &str) -> Option<Map<String, Value>> {
    match raw.trim().starts_with(['{', '[']).then(|| serde_json::from_str(raw)) {
        Some(Ok(Value::Object(body))) => Some(body),
        _ => None,
    }
}

/// One request sent and its answer read, by the route the PC's network gives its host.
/// Each wait has the limit, as a socket timeout does: a big answer on a slow link is
/// fine while its bytes keep coming.
pub async fn send(method: reqwest::Method, url: Url, headers: HeaderMap, body: Option<String>, timeout: Duration) -> Result<Answer, Refused> {
    send_on(client(route_of(&url).await).await?, method, url, headers, body, timeout).await
}

/// [`send`] through a client the caller made.
pub(super) async fn send_on(client: Client, method: reqwest::Method, url: Url, headers: HeaderMap, body: Option<String>, timeout: Duration) -> Result<Answer, Refused> {
    let mut sending = client.request(method, url).headers(headers);
    if let Some(body) = body {
        sending = sending.body(body);
    }
    let mut response = tokio::time::timeout(timeout, sending.send()).await.map_err(|_| timed_out())?.map_err(broken)?;
    let (status, headers) = (response.status().as_u16(), response.headers().clone());
    let mut bytes = Vec::new();
    while let Some(chunk) = tokio::time::timeout(timeout, response.chunk()).await.map_err(|_| timed_out())?.map_err(broken)? {
        bytes.extend_from_slice(&chunk);
    }
    let raw = String::from_utf8_lossy(&bytes).into_owned();
    Ok(Answer { status, parsed: parsed(&raw), raw, headers })
}

/// `_session_cookie_header`: the website session's cookies, empty with none.
pub(super) fn session_cookies(login: &Value) -> String {
    let pair = |name: &str, value: &str| {
        let (name, value) = (text(&login[name]), text(&login[value]));
        (!name.is_empty() && !value.is_empty()).then(|| format!("{name}={value}"))
    };
    [pair("session_name", "session_value"), pair("csrf_name", "csrf_value")].into_iter().flatten().collect::<Vec<_>>().join("; ")
}

/// `api_request`: one call as the signed-in account (its device key first, then its
/// website session), or as nobody when `allow_anonymous` and there is no login.
pub async fn request(api: &Api, call: Call<'_>) -> Result<Answer, Refused> {
    let store = api.repos.store().await.map_err(|error| Refused::new(failed(error), "error"))?;
    let login = account::login(store).await.map_err(|error| Refused::new(failed(error), "error"))?;
    let mut base = text(&login["base_url"]).trim_end_matches('/').to_string();
    if base.is_empty() {
        if !call.allow_anonymous {
            return Err(Refused::new("Not logged in", "not_logged_in"));
        }
        base = resolve_base_url(api).await?;
    }
    let path = if call.path.starts_with('/') { call.path.to_string() } else { format!("/{}", call.path) };
    let url = Url::parse(&format!("{base}{path}")).map_err(network)?;
    let method = reqwest::Method::from_bytes(call.method.to_uppercase().as_bytes()).map_err(network)?;

    let secret = |name: &str| if call.anonymous { String::new() } else { text(&login[name]) };
    let (device_key, session) = (secret("device_key"), secret("session_value"));
    let mut headers = HeaderMap::new();
    let mut put = |name: &'static str, value: &str| HeaderValue::from_str(value).map(|value| drop(headers.insert(name, value))).map_err(network);
    put("accept", "application/json")?;
    put("user-agent", &USER_AGENT)?;
    // The site's collect endpoints want an Origin that is its own host.
    put("origin", &base)?;
    let used_bearer = !device_key.is_empty();
    if used_bearer {
        put("authorization", &format!("Bearer {device_key}"))?;
    } else if !session.is_empty() {
        let cookies = session_cookies(&login);
        if !cookies.is_empty() {
            put("cookie", &cookies)?;
        }
        let csrf = text(&login["csrf_value"]);
        if !csrf.is_empty() && ["POST", "PUT", "PATCH", "DELETE"].contains(&method.as_str()) {
            put("x-csrf-token", &csrf)?;
        }
    } else if !call.allow_anonymous && !call.anonymous {
        return Err(Refused::new("Not logged in", "not_logged_in"));
    }
    if call.body.is_some() {
        put("content-type", "application/json")?;
    }

    let answer = send(method, url, headers, call.body.map(dumps), call.timeout).await?;
    if answer.status == 401 && (used_bearer || !session.is_empty()) {
        // A paired PC's device key is only valid on the desktop plugin; the Store and the
        // account pages answer 401 because they want a website cookie. A key the plugin
        // itself refuses is dead, and so is a refused session: neither is kept.
        return Err(match used_bearer {
            true if path.contains("/api/v1/plugins/uefn-ducky/collect/") => {
                let _ = super::account::unpair(api).await;
                Refused::new("This PC was disconnected", "device_unpaired")
            }
            true => Refused::new("That page needs a website sign-in. This PC stays connected.", "session_required"),
            false => {
                let _ = super::account::sign_out(api).await;
                Refused::new("Session expired — log in again", "session_expired")
            }
        });
    }
    Ok(answer)
}

/// `(kind, sentence)` for an answer that is not a usable one (`_site_failure`). Kind
/// `refused` is the site's own answer; the others are a firewall, an outage or a web
/// page where JSON was expected.
pub fn site_failure(answer: &Answer) -> Option<(String, String)> {
    let status = answer.status;
    let header = |name: &str| answer.headers.get(name).and_then(|value| value.to_str().ok()).unwrap_or_default();
    let html = answer.raw.trim_start().starts_with('<');
    let start: String = answer.raw.chars().take(4000).collect();
    let cloudflare = !header("cf-mitigated").is_empty() || (html && format!("{}{start}", header("server")).to_lowercase().contains("cloudflare"));
    let ok = (200..300).contains(&status);
    let page = html && answer.parsed.is_none();
    if ok && !page {
        return None;
    }
    let told = |kind: &str, message: String| Some((kind.to_string(), message));
    if cloudflare {
        return told("cloudflare_block", format!("uefnducky.org's firewall blocked this PC (HTTP {status}). Try again in a minute, or on another network or VPN."));
    }
    if status >= 500 {
        return told(&format!("http_{status}"), format!("uefnducky.org is having trouble (HTTP {status}). Try again in a minute."));
    }
    if ok || page {
        let message = format!("uefnducky.org sent a web page instead of an answer (HTTP {status}) — a firewall, proxy or Wi-Fi sign-in page may be in the way.");
        return told("html_page", message);
    }
    match status {
        404 => told("http_404", "uefnducky.org's sign-in isn't answering (HTTP 404). Try again in a minute.".to_string()),
        429 => told("http_429", "uefnducky.org is busy (HTTP 429). Try again in a minute.".to_string()),
        _ => told("refused", format!("uefnducky.org refused the request (HTTP {status}).")),
    }
}

/// One collect event of a site plugin (`_plugin_collect`'s arguments).
pub struct Collect<'a> {
    pub plugin: &'a str,
    pub event: &'a str,
    /// The code of a 404 (the plugin is not on this site), and of any other failure.
    pub unavailable_code: &'a str,
    pub error_code: &'a str,
    pub allow_anonymous: bool,
    pub timeout: Duration,
    /// Extra tries (1 s, 2 s apart) when the site cannot be reached or answers with an
    /// outage, a firewall page or a 404: only for calls that are safe to send twice.
    pub retries: u32,
}

/// `_plugin_collect`: post one event and answer its payload.
/// ponytail: a retried call's failure is not written to the support log (Python's
/// `record_activity`); it is, once that log is written here.
pub async fn collect(api: &Api, what: Collect<'_>, body: &Value) -> Result<Map<String, Value>, Refused> {
    let path = format!("/api/v1/plugins/{}/collect/{}", what.plugin, what.event);
    let mut attempt = 0;
    let (answer, failure) = loop {
        let last = attempt >= what.retries;
        let call = Call { method: "POST", path: &path, body: Some(body), allow_anonymous: what.allow_anonymous, anonymous: false, timeout: what.timeout };
        match request(api, call).await {
            Ok(answer) => {
                let failure = site_failure(&answer);
                // The site's own refusal is its answer: asking again changes nothing.
                if last || failure.as_ref().is_none_or(|(kind, _)| kind == "refused") {
                    break (answer, failure);
                }
            }
            Err(refused) if refused.code != "network" => return Err(refused),
            Err(refused) if last => {
                return Err(match refused.message.to_lowercase().contains("timed out") {
                    true => Refused::new("uefnducky.org didn't answer in time. Check your internet and try again.", what.error_code),
                    false => refused,
                })
            }
            Err(_) => {}
        }
        attempt += 1;
        tokio::time::sleep(Duration::from_secs(attempt.into())).await;
    };
    if let Some((kind, mut message)) = failure {
        if kind == "refused" {
            let body = answer.parsed.as_ref();
            let inner = body.and_then(|body| body.get("payload")).and_then(Value::as_object).and_then(|payload| payload.get("error")).filter(|error| truthy(error));
            let said = inner.or(body.and_then(|body| body.get("error"))).map(text).unwrap_or_default();
            // Never the site's own words about plugins or tenants.
            let lower = said.to_lowercase();
            if !said.is_empty() && !lower.contains("plugin") && !lower.contains("tenant") {
                message = said;
            }
        }
        return Err(Refused::new(message, if answer.status == 404 { what.unavailable_code } else { what.error_code }));
    }
    let body = answer.parsed.unwrap_or_default();
    Ok(match body.get("payload") {
        Some(Value::Object(payload)) => payload.clone(),
        _ => body,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn answer(status: u16, raw: &str, headers: &[(&'static str, &'static str)]) -> Answer {
        let headers = headers.iter().map(|(name, value)| (reqwest::header::HeaderName::from_static(name), HeaderValue::from_static(value))).collect();
        Answer { status, parsed: parsed(raw), raw: raw.to_string(), headers }
    }

    #[test]
    fn an_account_address_is_https_and_its_host() {
        assert_eq!(normalize_base_url("  "), Ok(DEFAULT_BASE_URL.to_string()));
        assert_eq!(normalize_base_url("example.org/"), Ok("https://example.org".to_string()));
        assert_eq!(normalize_base_url("HTTPS://Example.org:8443/path?x#y"), Ok("https://Example.org:8443".to_string()));
        assert_eq!(normalize_base_url("http://example.org").unwrap_err().code, "invalid_url");
        assert_eq!(normalize_base_url("https:///path").unwrap_err().message, "Account URL is missing a host");
        assert!(USER_AGENT.starts_with("UEFN-Ducky/") && USER_AGENT.matches('.').count() == 2, "{}", *USER_AGENT);
    }

    #[test]
    fn only_a_json_object_is_parsed() {
        assert_eq!(parsed(" {\"a\": 1}\n").unwrap()["a"], 1);
        assert!(parsed("[1]").is_none() && parsed("<html>").is_none() && parsed("{broken").is_none() && parsed("").is_none());
    }

    #[test]
    fn an_answer_that_is_not_one_says_what_is_in_the_way() {
        let kind = |answer: Answer| site_failure(&answer).map(|(kind, _)| kind).unwrap_or_default();
        assert_eq!(kind(answer(200, "{\"ok\": true}", &[])), "");
        assert_eq!(kind(answer(204, "", &[])), "");
        assert_eq!(kind(answer(200, " <html>sign in</html>", &[])), "html_page");
        assert_eq!(kind(answer(403, "<html>Cloudflare</html>", &[])), "cloudflare_block");
        assert_eq!(kind(answer(403, "{}", &[("cf-mitigated", "challenge")])), "cloudflare_block");
        assert_eq!(kind(answer(403, "<html>no</html>", &[("server", "cloudflare")])), "cloudflare_block");
        assert_eq!(kind(answer(503, "{}", &[])), "http_503");
        assert_eq!(kind(answer(404, "{}", &[])), "http_404");
        assert_eq!(kind(answer(429, "{}", &[])), "http_429");
        assert_eq!(kind(answer(400, "<p>bad</p>", &[])), "html_page");
        let (kind, message) = site_failure(&answer(403, "{\"error\": \"no\"}", &[])).unwrap();
        assert_eq!((kind.as_str(), message.as_str()), ("refused", "uefnducky.org refused the request (HTTP 403)."));
    }
}
