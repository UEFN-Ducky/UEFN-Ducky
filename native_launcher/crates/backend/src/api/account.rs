//! The Account tab's reads and the login row's writes (`frontend/duckyos_account.py`:
//! `refresh_status`, `get_status`, `list_account_pcs`, `revoke_account_pc`, `_save_blob`).
//!
//! Every write of the login row goes through [`save_login`], so a sign-out, an expired
//! session and an unpaired PC all drop the old account's data key copy and tell the
//! windows who is signed in now.
//! ponytail: signing in is still the Python backend's, so `browser_pending` and
//! `user_code` (a sign-in waiting in the browser) are always empty here, and an unpair
//! does not stop the presence beat or the remote mailbox; they move here with sign-in.
//! ponytail: plugins that listen for account changes, the spaces and the new account's
//! data key fetch are told by the Python backend only; they move with the plugin host
//! and the data key.
//! ponytail: `duckyos_open_site` opens the person's browser and stays Python's.

use std::sync::Arc;
use std::time::Duration;

use reqwest::header::{HeaderMap, HeaderValue};
use reqwest::{Method, Url};
use serde_json::{json, Map, Value};

use super::chats::removed;
use super::settings::failed;
use super::site::{collect, network, resolve_base_url, send, session_cookies, text, Answer, Collect, Refused, DEFAULT_BASE_URL, USER_AGENT};
use super::Api;
use crate::store::pyjson::{int, pystr, truthy};
use crate::store::{account, account_rows, seal, secrets};

type Fields = Map<String, Value>;

/// The login row as an object (`_load_blob`): empty signed out.
async fn stored(api: &Api) -> Result<Value, String> {
    let store = api.repos.store().await.map_err(failed)?;
    let login = account::login(store).await.map_err(failed)?;
    Ok(if login.is_object() { login } else { json!({}) })
}

/// `_save_blob` and `_clear_blob` (an empty `login`): the one place the login row is
/// written. Empty fields are not kept.
pub(super) async fn save_login(api: &Api, mut login: Value) -> Result<(), String> {
    let store = api.repos.store().await.map_err(failed)?;
    let before = account::login(store).await.map_err(failed)?;
    let fields = login.as_object_mut().ok_or("a login is an object")?;
    fields.retain(|_, value| !(value.is_null() || [json!(""), json!([]), json!({})].contains(value)));
    if fields.is_empty() {
        secrets::delete(store, secrets::LOGIN_NAME.to_string()).await.map_err(failed)?;
    } else {
        let text = login.to_string();
        let sealed = tokio::task::spawn_blocking(move || seal::protect_secret(&text)).await.map_err(|stopped| stopped.to_string())?.map_err(failed)?;
        secrets::set_blob(store, secrets::LOGIN_NAME.to_string(), sealed).await.map_err(failed)?;
    }
    let (was, now) = (account::key_of(&before), account::key_of(&login));
    if was != now || before["device_key"] != login["device_key"] {
        // The data key's copy is wrapped with the old device token.
        let copy = api.repos.data_dir().join(account::KEY_CACHE_NAME);
        #[allow(clippy::disallowed_methods)] // on a blocking thread
        let _ = tokio::task::spawn_blocking(move || std::fs::remove_file(copy)).await;
    }
    if was != now {
        // Chats are the signed-in account's: every window drops the old account's at once.
        let (chats, folders) = account_rows::owned_ids(store, account::account_id_for(&was)).await.unwrap_or_default();
        api.publish(vec![json!({"type": "duckyos_account_changed", "account": now}), removed(&chats, &folders)]);
    }
    Ok(())
}

/// `_unpair_this_pc`: this PC's device key goes, a website session stays.
pub(super) async fn unpair(api: &Api) -> Result<(), String> {
    let mut login = stored(api).await?;
    if let Some(fields) = login.as_object_mut() {
        fields.shift_remove("device_key");
        fields.shift_remove("device_key_id");
        fields.shift_remove("device_key_error");
    }
    save_login(api, login).await
}

/// `_clear_expired_auth`: a dead website session and no device key is signed out.
pub(super) async fn sign_out(api: &Api) -> Result<(), String> {
    save_login(api, json!({})).await
}

/// `get_status`: what the windows may know of the login, never its secrets.
async fn status(api: &Api, login: &Value) -> Result<Value, String> {
    let base = match truthy(&login["base_url"]) {
        true => pystr(&login["base_url"]),
        false => resolve_base_url(api).await.map_err(|refused| refused.message)?,
    };
    let device = truthy(&login["device_key"]);
    let logged_in = device || truthy(&login["session_value"]);
    let said = |name: &str| if logged_in { text(&login[name]) } else { String::new() };
    // ponytail: a list saved as anything but a list reads as none (Python lists a string's letters).
    let list = |name: &str| if logged_in && login[name].is_array() { login[name].clone() } else { json!([]) };
    Ok(json!({
        "logged_in": logged_in,
        "needs_code": false,
        "browser_pending": false,
        "user_code": "",
        "base_url": base,
        "default_base_url": DEFAULT_BASE_URL,
        "email": said("email"),
        "display_name": said("display_name"),
        "user_id": said("user_id"),
        "account": account::key_of(login),
        "roles": list("roles"),
        "permissions": list("permissions"),
        "device_key_active": device,
        "device_key_error": said("device_key_error"),
        "auth_mode": "browser",
        "agent_denied": list("agent_denied"),
        "agent_settings": if logged_in && login["agent_settings"].is_object() { login["agent_settings"].clone() } else { json!({}) },
    }))
}

/// `_request` as the website session: one call with its cookies, and its CSRF token
/// when `csrf`.
/// ponytail: cookies set on a redirect's way are not kept (Python's cookie jar keeps
/// them); the site's account pages answer without one.
async fn ask(login: &Value, method: Method, path: &str, csrf: bool, timeout: Duration) -> Result<Answer, Refused> {
    let url = Url::parse(&format!("{}{path}", text(&login["base_url"]).trim_end_matches('/'))).map_err(network)?;
    let mut headers = HeaderMap::new();
    let mut put = |name: &'static str, value: &str| HeaderValue::from_str(value).map(|value| drop(headers.insert(name, value))).map_err(network);
    put("accept", "application/json, text/html;q=0.9,*/*;q=0.8")?;
    put("user-agent", &USER_AGENT)?;
    let cookies = session_cookies(login);
    if !cookies.is_empty() {
        put("cookie", &cookies)?;
    }
    let token = text(&login["csrf_value"]);
    if csrf && !token.is_empty() {
        put("x-csrf-token", &token)?;
    }
    send(method, url, headers, None, timeout).await
}

/// `_parse_set_cookie`: name and value of each cookie an answer sets, a repeated name's
/// last value at its first place.
fn cookies_set(headers: &HeaderMap) -> Vec<(String, String)> {
    let mut cookies: Vec<(String, String)> = Vec::new();
    let mut keep = |name: &str, value: &str, replace: bool| match cookies.iter_mut().find(|(kept, _)| kept == name) {
        Some(kept) if replace => kept.1 = value.to_string(),
        Some(_) => {}
        None => cookies.push((name.to_string(), value.to_string())),
    };
    for raw in headers.get_all("set-cookie") {
        let raw = String::from_utf8_lossy(raw.as_bytes());
        if let Some((name, value)) = raw.split(';').next().unwrap_or_default().trim().split_once('=') {
            keep(name.trim(), value.trim(), true);
        }
    }
    // The site also says the CSRF token in a header; a cookie of that name wins.
    if let Some(token) = headers.get("x-ducky-csrf-token").and_then(|value| value.to_str().ok()).filter(|token| !token.is_empty()) {
        keep("duckyos_csrf", token.trim(), false);
    }
    cookies
}

/// `_apply_session_cookies`: the session, CSRF and login challenge cookies an answer
/// set, kept under the names the site gave them (`duckyos_session`, `duckyos_session_<site>`).
fn apply_cookies(login: &mut Value, headers: &HeaderMap) {
    let cookies = cookies_set(headers);
    for (cookie, field) in [("duckyos_session", "session"), ("duckyos_csrf", "csrf"), ("duckyos_login_challenge", "challenge")] {
        let named = |name: &str| name.to_lowercase().strip_prefix(cookie).is_some_and(|rest| rest.is_empty() || rest.starts_with('_'));
        if let Some((name, value)) = cookies.iter().find(|(name, value)| named(name) && !value.is_empty()) {
            login[format!("{field}_name")] = json!(name);
            login[format!("{field}_value")] = json!(value);
        }
    }
}

/// `str(a or b or "")`.
fn first(values: &[&Value]) -> String {
    values.iter().find(|value| truthy(value)).map(|value| pystr(value)).unwrap_or_default()
}

fn strings(list: &[Value]) -> Value {
    list.iter().map(pystr).collect::<Vec<_>>().into()
}

/// `fetch_me` then `fetch_permissions`: who the website session is, and what they may do.
async fn identity(login: &mut Value) -> Result<(), Refused> {
    let wait = Duration::from_secs(20);
    let answer = ask(login, Method::GET, "/api/v1/auth/me", false, wait).await?;
    apply_cookies(login, &answer.headers);
    if answer.status == 401 {
        return Err(Refused::new("Session expired — log in again", "session_expired"));
    }
    let Some(me) = answer.parsed.filter(|_| answer.status == 200) else {
        return Err(Refused::new(format!("Could not load account (HTTP {})", answer.status), "me_failed"));
    };
    let of = |name: &str| me.get(name).unwrap_or(&Value::Null);
    login["email"] = first(&[of("email"), &login["email"]]).into();
    login["user_id"] = first(&[of("userId"), of("user_id")]).into();
    login["display_name"] = first(&[of("displayName"), of("display_name")]).into();
    if let Value::Array(roles) = of("roles") {
        login["roles"] = strings(roles);
    }
    let answer = ask(login, Method::GET, "/api/v1/acl/self", false, wait).await?;
    apply_cookies(login, &answer.headers);
    if let Some(Value::Array(permissions)) = answer.parsed.filter(|_| answer.status == 200).as_ref().and_then(|body| body.get("permissions")) {
        login["permissions"] = strings(permissions);
    }
    Ok(())
}

/// Who is signed in (`refresh_status`). A website session is asked who it is, so a role
/// granted since sign-in shows; a PC with only its device key answers from the login row.
pub async fn duckyos_get_status(api: Arc<Api>, _args: Fields) -> Result<Value, String> {
    let login = stored(&api).await?;
    if !truthy(&login["session_value"]) {
        return status(&api, &login).await;
    }
    let mut fresh = login.clone();
    let refused = match identity(&mut fresh).await {
        Ok(()) => {
            save_login(&api, fresh).await?;
            return status(&api, &stored(&api).await?).await;
        }
        Err(refused) => refused,
    };
    if refused.code != "session_expired" {
        let mut status = status(&api, &login).await?;
        status["error"] = refused.message.into();
        return Ok(status);
    }
    // The session is dead: what pairs this PC stays, and who it was for the sign-in page.
    let mut kept = Map::new();
    for name in ["base_url", "email", "device_key", "device_key_id"].into_iter().filter(|name| truthy(&login[*name])) {
        kept.insert(name.to_string(), login[name].clone());
    }
    save_login(&api, kept.into()).await?;
    let mut status = status(&api, &stored(&api).await?).await?;
    if status["logged_in"] == false {
        status["session_expired"] = true.into();
        status["error"] = refused.message.into();
    }
    Ok(status)
}

/// `str(value or "").strip()`.
fn stripped(value: &Value) -> String {
    text(value).trim().to_string()
}

fn pcs_call<'a>(event: &'a str, error_code: &'a str) -> Collect<'a> {
    Collect { plugin: "uefn-ducky", event, unavailable_code: "rpc_unavailable", error_code, allow_anonymous: false, timeout: Duration::from_secs(20) }
}

/// The PCs linked to this account (`list_account_pcs`).
/// ponytail: a `last_seen` that is no number reads as 0 (Python refuses the whole list).
pub async fn duckyos_list_pcs(api: Arc<Api>, _args: Fields) -> Result<Value, String> {
    let mine = stripped(&stored(&api).await?["device_key_id"]);
    let answer = match collect(&api, pcs_call("desktop-devices", "devices_failed"), &json!({})).await {
        Ok(answer) => answer,
        Err(refused) => return Ok(json!({"ok": false, "error": refused.message, "code": refused.code, "devices": []})),
    };
    let rows = answer.get("devices").and_then(Value::as_array).map(Vec::as_slice).unwrap_or_default();
    let pc = |row: &Value| {
        let key_id = first(&[&row["keyId"], &row["key_id"]]).trim().to_string();
        let name = if truthy(&row["name"]) { pystr(&row["name"]) } else { "UEFN Ducky".to_string() };
        json!({"keyId": key_id, "name": name, "live": truthy(&row["live"]), "last_seen": int(&row["last_seen"]), "mine": !mine.is_empty() && key_id == mine})
    };
    let devices: Vec<Value> = rows.iter().filter(|row| row.is_object()).map(pc).collect();
    Ok(json!({"ok": true, "devices": devices}))
}

/// Drop one linked PC (`revoke_account_pc`); this PC's own key is forgotten here too.
pub async fn duckyos_revoke_pc(api: Arc<Api>, args: Fields) -> Result<Value, String> {
    let refused = |message: &str, code: &str| -> Result<Value, String> { Ok(json!({"ok": false, "error": message, "code": code, "logged_in": false})) };
    let key_id = stripped(args.get("key_id").unwrap_or(&Value::Null));
    if key_id.is_empty() {
        return refused("No PC selected", "no_key");
    }
    let login = stored(&api).await?;
    if truthy(&login["base_url"]) && truthy(&login["session_value"]) {
        // The website's own key list; the plugin's answer below is the one that counts.
        let _ = ask(&login, Method::DELETE, &format!("/api/v1/auth/api-keys/{key_id}"), true, Duration::from_secs(12)).await;
    }
    if let Err(no) = collect(&api, pcs_call("desktop-device-revoke", "revoke_failed"), &json!({"keyId": key_id})).await {
        return refused(&no.message, &no.code);
    }
    if key_id == stripped(&login["device_key_id"]) {
        unpair(&api).await?;
    }
    let mut answer = json!({"ok": true});
    if let (Some(answer), Value::Object(status)) = (answer.as_object_mut(), status(&api, &stored(&api).await?).await?) {
        answer.extend(status);
    }
    Ok(answer)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn an_answers_cookies_land_in_the_login() {
        let mut headers = HeaderMap::new();
        for cookie in ["DuckyOS_Session_site = one ; Path=/; HttpOnly", "other=x", "duckyos_session_site=ignored-other-name", "duckyos_csrf_site=", "no-equals", "DuckyOS_Session_site=two"] {
            headers.append("set-cookie", HeaderValue::from_static(cookie));
        }
        headers.insert("x-ducky-csrf-token", HeaderValue::from_static(" from-header "));
        let mut login = json!({"csrf_value": "old"});
        apply_cookies(&mut login, &headers);
        // A name's last value counts, at its first place; an empty cookie is no cookie.
        assert_eq!((login["session_name"].as_str(), login["session_value"].as_str()), (Some("DuckyOS_Session_site"), Some("two")));
        assert_eq!((login["csrf_name"].as_str(), login["csrf_value"].as_str()), (Some("duckyos_csrf"), Some("from-header")));
        assert!(login.get("challenge_name").is_none());
        // Names that only start alike are not the site's.
        let mut login = json!({});
        let mut headers = HeaderMap::new();
        headers.append("set-cookie", HeaderValue::from_static("duckyos_sessionx=1; duckyos_session=inside"));
        headers.append("set-cookie", HeaderValue::from_static("duckyos_login_challenge=c"));
        apply_cookies(&mut login, &headers);
        assert_eq!(login, json!({"challenge_name": "duckyos_login_challenge", "challenge_value": "c"}));
    }
}
