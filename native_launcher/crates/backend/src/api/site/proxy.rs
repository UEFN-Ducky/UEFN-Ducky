//! Which proxy a URL takes when the PC uses proxy auto-config (a PAC script or WPAD,
//! common on school and work networks): what the browser asks WinHTTP
//! (`frontend/net_access.py::PacProxyHandler`). Blocking: call from a blocking task.

/// The first proxy for `scheme` in a WinHTTP list (`"p:80"`, `"http=p:80;https=q:443"`,
/// `"a:1 b:2"`), as a URL; empty for none (`net_access.pick_proxy`).
pub fn pick(answer: &str, scheme: &str) -> String {
    for part in answer.replace(' ', ";").split(';') {
        let mut part = part.trim();
        if let Some((key, rest)) = part.split_once('=') {
            if key.trim().to_lowercase() != scheme {
                continue;
            }
            part = rest;
        }
        if !part.is_empty() {
            return if part.contains("://") { part.to_string() } else { format!("http://{part}") };
        }
    }
    String::new()
}

/// The environment's proxy for `scheme` (`https_proxy`, `HTTPS_PROXY`), as curl reads it.
fn from_environment(scheme: &str) -> String {
    let named = |name: String| std::env::var(name).ok().filter(|proxy| !proxy.trim().is_empty());
    named(format!("{scheme}_proxy")).or_else(|| named(format!("{scheme}_PROXY"))).unwrap_or_default()
}

/// The fixed proxy `scheme` takes with no auto-config: the environment's, then Internet
/// Options' (`urllib.request.getproxies`). Empty for none.
/// ponytail: the hosts a fixed proxy is bypassed for are the client's to follow, not
/// named here; and macOS's System Settings proxy is not read (the client follows it).
pub fn fixed(scheme: &str) -> String {
    let named = from_environment(scheme);
    #[cfg(windows)]
    if named.is_empty() {
        return pick(&config().fixed, scheme);
    }
    named
}

/// `none`, `static` or `pac`: how this PC reaches the internet (`net_access.proxy_mode`).
pub fn mode() -> &'static str {
    #[cfg(windows)]
    if config().script.is_some() {
        return "pac";
    }
    if ["http", "https", "all"].iter().any(|scheme| !fixed(scheme).is_empty()) { "static" } else { "none" }
}

/// WinHTTP's proxies for `url` (`"host:port;..."`); empty when the PC has no auto-config,
/// the lookup failed or the URL goes direct.
#[cfg(not(windows))]
pub fn auto_config_answer(_url: &str) -> String {
    // No WinHTTP: the environment's proxy (and macOS's system one) is the client's default.
    String::new()
}

/// Internet Options' proxy settings: detect an auto-config, its script's URL (NUL-ended),
/// and the fixed proxy list.
#[cfg(windows)]
struct Config {
    detect: bool,
    script: Option<Vec<u16>>,
    fixed: String,
}

/// A copy of a string WinHTTP allocated (with its NUL), which is then freed.
#[cfg(windows)]
unsafe fn take(text: *mut u16) -> Option<Vec<u16>> {
    if text.is_null() {
        return None;
    }
    let len = (0..).take_while(|&at| *text.add(at) != 0).count();
    let copy = std::slice::from_raw_parts(text, len + 1).to_vec();
    windows_sys::Win32::Foundation::GlobalFree(text.cast());
    Some(copy)
}

/// Read once, as the Python backend does: a changed setting is seen at the next start.
#[cfg(windows)]
fn config() -> &'static Config {
    use windows_sys::Win32::Networking::WinHttp::{WinHttpGetIEProxyConfigForCurrentUser, WINHTTP_CURRENT_USER_IE_PROXY_CONFIG};

    static CONFIG: std::sync::OnceLock<Config> = std::sync::OnceLock::new();
    CONFIG.get_or_init(|| unsafe {
        let mut ie: WINHTTP_CURRENT_USER_IE_PROXY_CONFIG = std::mem::zeroed();
        if WinHttpGetIEProxyConfigForCurrentUser(&mut ie) == 0 {
            return Config { detect: false, script: None, fixed: String::new() };
        }
        let script = take(ie.lpszAutoConfigUrl).filter(|url| url.len() > 1);
        let fixed = take(ie.lpszProxy).map(|proxy| String::from_utf16_lossy(&proxy[..proxy.len() - 1])).unwrap_or_default();
        take(ie.lpszProxyBypass);
        Config { detect: ie.fAutoDetect != 0, script, fixed }
    })
}

#[cfg(windows)]
pub fn auto_config_answer(url: &str) -> String {
    use std::ptr::null;

    use windows_sys::Win32::Networking::WinHttp::{
        WinHttpCloseHandle, WinHttpGetProxyForUrl, WinHttpOpen, WINHTTP_ACCESS_TYPE_NAMED_PROXY, WINHTTP_ACCESS_TYPE_NO_PROXY,
        WINHTTP_AUTOPROXY_AUTO_DETECT, WINHTTP_AUTOPROXY_CONFIG_URL, WINHTTP_AUTOPROXY_OPTIONS, WINHTTP_AUTO_DETECT_TYPE_DHCP,
        WINHTTP_AUTO_DETECT_TYPE_DNS_A, WINHTTP_PROXY_INFO,
    };

    let config = config();
    if !config.detect && config.script.is_none() {
        return String::new();
    }
    let wide = |text: &str| text.encode_utf16().chain([0]).collect::<Vec<u16>>();
    let (agent, url) = (wide("UEFN-Ducky"), wide(url));
    unsafe {
        let session = WinHttpOpen(agent.as_ptr(), WINHTTP_ACCESS_TYPE_NO_PROXY, null(), null(), 0);
        if session.is_null() {
            return String::new();
        }
        let mut options: WINHTTP_AUTOPROXY_OPTIONS = std::mem::zeroed();
        match &config.script {
            Some(script) => {
                options.dwFlags = WINHTTP_AUTOPROXY_CONFIG_URL;
                options.lpszAutoConfigUrl = script.as_ptr();
            }
            None => {
                options.dwFlags = WINHTTP_AUTOPROXY_AUTO_DETECT;
                options.dwAutoDetectFlags = WINHTTP_AUTO_DETECT_TYPE_DHCP | WINHTTP_AUTO_DETECT_TYPE_DNS_A;
            }
        }
        options.fAutoLogonIfChallenged = 1;
        let mut info: WINHTTP_PROXY_INFO = std::mem::zeroed();
        let found = WinHttpGetProxyForUrl(session, url.as_ptr(), &mut options, &mut info) != 0;
        WinHttpCloseHandle(session);
        if !found {
            return String::new();
        }
        let proxy = take(info.lpszProxy);
        take(info.lpszProxyBypass);
        match proxy {
            Some(proxy) if info.dwAccessType == WINHTTP_ACCESS_TYPE_NAMED_PROXY => String::from_utf16_lossy(&proxy[..proxy.len() - 1]),
            _ => String::new(),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::pick;

    #[test]
    fn the_first_proxy_for_the_scheme_is_picked() {
        assert_eq!(pick("p:80", "https"), "http://p:80");
        assert_eq!(pick("http=p:80;https=q:443", "https"), "http://q:443");
        assert_eq!(pick("http=p:80;HTTPS=q:443", "https"), "http://q:443");
        assert_eq!(pick("a:1 b:2", "http"), "http://a:1");
        assert_eq!(pick("https://secure:8443", "https"), "https://secure:8443");
        assert_eq!(pick("http=p:80", "https"), "");
        assert_eq!(pick(" ; ", "https"), "");
    }
}
