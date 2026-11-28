//! A chat's messages as the rows the chat view draws (`chat_rows.py::_messages_to_ui`
//! under `panel_chats/messages.py`): a page of rows, one tool row's whole result, and
//! one tool row's file edits with their texts.
//!
//! ponytail: three things are still the Python backend's, which answers these calls
//! until they are here. A running turn (its pending tools are one row and it is never
//! "interrupted") needs the agent runner. An attachment stored as a file comes back as
//! it is stored, not opened: its picture or text is checked against the ignore rules
//! first. A tool row saved before edits were stored with it gets no file edit: that one
//! is worked out from the editor's file cache and history.

use std::sync::Arc;

use serde_json::{json, Value};

use super::groups::{arg, refused};
use super::{float, int, pystr, sidebar, text, truthy, Api, Fields, Sidebar};
use crate::store::pyjson::dumps;

/// Row id = message index * this + row index within the message, so a page knows its
/// ids without converting the messages before it.
const ROW_ID_STEP: i64 = 1 << 32;
/// A tool card's preview; past it the card asks `load_tool_result` for the rest.
const TOOL_RESULT_CAP: usize = 8_000;
/// A file edit's before and after texts travel with its row only when they are this
/// small together; a bigger one is asked for when its diff is opened.
const FILE_EDIT_INLINE_CHARS: usize = 4_000;

/// `_tool_result_text`: what a tool card shows as the result.
fn result_text(result: &Value) -> String {
    if !truthy(result) {
        return String::new();
    }
    match ["data", "error"].iter().find(|key| truthy(&result[**key])) {
        Some(key) => pystr(&result[*key]),
        None => dumps(result),
    }
}

fn author(message: &Value) -> Option<Value> {
    let author = message.get("author").filter(|author| author.is_object() && truthy(author))?;
    let field = |name: &str| text(&author[name]).trim().to_string();
    Some(json!({
        "name": field("name"),
        "member_conv_id": field("member_conv_id"),
        "tts_voice": field("tts_voice"),
        "tts_speed": float(&author["tts_speed"]).unwrap_or(0.0),
        "color": field("color"),
        "profile_id": field("profile_id"),
    }))
}

/// A tool call's two rows: the call, then how it ended.
fn tool_rows(block: &Value, id: i64, out: &mut Vec<Value>) {
    let name = block.get("name").cloned().unwrap_or(json!("?"));
    let args = if truthy(&block["arguments"]) { block["arguments"].clone() } else { json!({}) };
    let mut short = dumps(&args);
    if short.chars().count() > 120 {
        short = short.chars().take(117).collect::<String>() + "...";
    }
    let line = format!("⚙ {}({short})", pystr(&name));
    let call_id = text(&block["id"]);
    out.push(json!({"id": id, "role": "tool", "text": line, "tool": {"id": call_id, "name": name, "arguments": args, "status": "pending"}}));
    let status = block.get("status").cloned().unwrap_or(json!("?"));
    let ms = int(&block["duration_ms"]);
    let empty = json!({});
    let result = if block["result"].is_object() { &block["result"] } else { &empty };
    let mut done = json!({
        "id": call_id, "name": name, "arguments": args, "status": status, "durationMs": ms,
        "result": result_text(result), "hint": text(&result["hint"]),
    });
    let exit_code = result.get("exit_code").unwrap_or(&block["exit_code"]);
    if exit_code.is_i64() || exit_code.is_u64() {
        done["exitCode"] = exit_code.clone();
    }
    if block["llm_tokens"].as_i64().is_some_and(|tokens| tokens > 0) {
        done["llmTokens"] = block["llm_tokens"].clone();
    }
    match (&block["file_edits"], &block["file_edit"]) {
        (Value::Array(many), _) => {
            if let Some(first) = many.first() {
                done["fileEdit"] = first.clone();
            }
            done["fileEdits"] = json!(many);
        }
        (_, one @ Value::Object(_)) => done["fileEdit"] = one.clone(),
        _ => {} // an edit saved without its texts: see the module note
    }
    let role = if status == "success" { "success" } else { "error" };
    out.push(json!({"id": id + 1, "role": role, "text": format!("{line} · {} · {ms}ms", pystr(&status)), "tool": done}));
}

/// One stored message as its rows, in order.
fn convert(index: i64, message: &Value) -> Vec<Value> {
    let mut out = Vec::new();
    let mut id = index * ROW_ID_STEP;
    let role = message.get("role").cloned().unwrap_or(json!("user"));
    let pictures = message["attachments"].as_array().filter(|pictures| !pictures.is_empty());
    let stored = |pictures: &Vec<Value>| Value::Array(pictures.iter().map(|one| if one.is_object() { one.clone() } else { json!({}) }).collect());
    if role == "user" {
        let said = message.get("text").or(message.get("content")).cloned().unwrap_or(json!(""));
        let mut row = json!({"id": id, "role": "user", "text": said});
        if let Some(pictures) = pictures {
            row["attachments"] = stored(pictures);
        }
        return vec![row];
    }
    if role != "assistant" {
        return out;
    }
    for block in message["blocks"].as_array().into_iter().flatten() {
        let said = text(&block["text"]);
        let row = match block["type"].as_str() {
            Some("tool_call") => {
                tool_rows(block, id, &mut out);
                id += 2;
                continue;
            }
            _ if said.trim().is_empty() => continue,
            // Per-step reasoning, shown between the tools.
            Some("thinking") => json!({"id": id, "role": "assistant", "text": "", "thinking": said}),
            // What the model said between tool calls, kept in order.
            Some("text") => json!({"id": id, "role": "assistant", "text": said}),
            // A message the person sent into this turn, where it landed.
            Some("user") => json!({"id": id, "role": "user", "text": said, "steered": true}),
            _ => continue,
        };
        out.push(row);
        id += 1;
    }
    let content = message.get("content").cloned().unwrap_or(json!(""));
    let thinking = message["thinking"].as_str().filter(|thinking| !thinking.trim().is_empty());
    let shown = truthy(&content) || pictures.is_some() || thinking.is_some();
    let incomplete = truthy(&message["incomplete"]);
    if !shown && !incomplete {
        return out;
    }
    // A turn that crashed with nothing of its own to say still shows that it was interrupted.
    let mut row = json!({"id": id, "role": "assistant", "text": if shown { content } else { json!("") }});
    if shown {
        if let Some(pictures) = pictures {
            row["attachments"] = stored(pictures);
        }
        if let Some(thinking) = thinking {
            row["thinking"] = json!(thinking);
        }
        // Walkthroughs this message offers as buttons.
        let tours: Vec<String> = message["tours"].as_array().into_iter().flatten().map(pystr).filter(|tour| !tour.trim().is_empty()).collect();
        if message["tours"].as_array().is_some_and(|tours| !tours.is_empty()) {
            row["tours"] = json!(tours);
        }
    }
    if let Some(author) = author(message) {
        row["author"] = author;
    }
    if incomplete {
        if let Some(error) = message["error"].as_str().filter(|error| !error.trim().is_empty()) {
            row["error"] = json!(error);
        }
        row["incomplete"] = json!(true);
        row["checkpoint"] = json!(true);
        row["run_id"] = message["run_id"].clone();
        row["live_run"] = json!(false);
    }
    out.push(row);
    out
}

/// `_cap_tool_results`: a long result is cut, with its whole size.
fn cap_result(tool: &mut Value) {
    let Some(result) = tool["result"].as_str().filter(|result| result.len() > TOOL_RESULT_CAP) else { return };
    if result.chars().count() > TOOL_RESULT_CAP {
        let (cut, bytes) = (result.chars().take(TOOL_RESULT_CAP).collect::<String>(), result.len());
        tool["result"] = json!(cut);
        tool["resultTruncated"] = json!(true);
        tool["resultBytes"] = json!(bytes);
    }
}

fn without_texts(edit: &mut Value) {
    let size = |side: &Value| side.as_str().map_or(0, |side| side.chars().count());
    if edit.is_object() && size(&edit["before"]) + size(&edit["after"]) > FILE_EDIT_INLINE_CHARS {
        edit["before"] = json!("");
        edit["after"] = json!("");
        edit["contentOmitted"] = json!(true);
    }
}

/// `_trim_file_edits`: big edits go as their path and line counts.
fn trim_edits(tool: &mut Value) {
    let first = match tool.get_mut("fileEdits").and_then(Value::as_array_mut).filter(|many| !many.is_empty()) {
        Some(many) => {
            many.iter_mut().for_each(without_texts);
            Some(many[0].clone())
        }
        None => None,
    };
    match (first, tool.get_mut("fileEdit").filter(|one| one.is_object())) {
        (Some(first), Some(one)) => *one = first, // the first edit is sent under both names
        (None, Some(one)) => without_texts(one),
        _ => {}
    }
}

impl Sidebar<'_> {
    /// The `limit` newest rows before `before` (every row without them), opening only
    /// the messages they come from. Nothing for a chat that is not this account's.
    async fn rows(&self, conv_id: &str, before: Option<i64>, limit: Option<i64>) -> Result<Vec<Value>, String> {
        let range = |start: i64, stop: Option<i64>| async move {
            self.chats.messages_range(self.who.clone(), conv_id.to_string(), start, stop).await.map_err(super::failed)
        };
        let stop = before.map(|before| (before.div_euclid(ROW_ID_STEP) + 1).max(0));
        let rows = |got: Vec<(i64, Value)>| {
            let rows = got.iter().flat_map(|(seq, message)| convert(*seq, message));
            rows.filter(|row| before.is_none_or(|before| row["id"].as_i64().unwrap_or(0) < before)).collect::<Vec<_>>()
        };
        let Some(limit) = limit.filter(|limit| *limit > 0).map(|limit| limit as usize) else { return Ok(rows(range(0, stop).await?.0)) };
        let mut want = (limit as i64 / 4).max(16); // an assistant turn gives many rows
        loop {
            let count = range(0, Some(0)).await?.1;
            let end = stop.map_or(count, |stop| stop.min(count));
            let start = (end - want).max(0);
            let mut page = rows(range(start, Some(end)).await?.0);
            if start == 0 || page.len() >= limit {
                return Ok(page.split_off(page.len().saturating_sub(limit)));
            }
            want *= 4;
        }
    }

    /// The tool card of one row; the refusal when the chat or the row has none.
    async fn tool(&self, args: &Fields, missing: &str) -> Result<Result<Value, Value>, String> {
        let conv_id = arg(args, "conv_id");
        if self.chat(&conv_id).await?.is_none() {
            return Ok(Err(refused("Chat not found")));
        }
        let row_id = args.get("row_id").map_or(0, int);
        let rows = self.rows(&conv_id, Some(row_id + 1), Some(1)).await?;
        Ok(match rows.last().filter(|row| row["id"] == row_id && row["tool"].is_object()) {
            Some(row) => Ok(row["tool"].clone()),
            None => Err(refused(missing)),
        })
    }
}

/// A page of a chat's rows: the `limit` newest, or the `limit` before `before_id`; the
/// whole chat without either. Long tool results and big file edits are cut unless
/// `full_results`.
pub async fn load_messages(api: Arc<Api>, args: Fields) -> Result<Value, String> {
    let sidebar = sidebar(&api).await?;
    let number = |name: &str| args.get(name).filter(|value| !value.is_null()).map(int);
    let mut rows = sidebar.rows(&arg(&args, "conv_id"), number("before_id"), number("limit")).await?;
    if !args.get("full_results").is_some_and(truthy) {
        for tool in rows.iter_mut().filter_map(|row| row.get_mut("tool")).filter(|tool| tool.is_object()) {
            cap_result(tool);
            trim_edits(tool);
        }
    }
    Ok(Value::Array(rows))
}

/// The whole result of one tool row that `load_messages` cut.
pub async fn load_tool_result(api: Arc<Api>, args: Fields) -> Result<Value, String> {
    Ok(match sidebar(&api).await?.tool(&args, "No tool result in that row").await? {
        Ok(tool) => json!({"ok": true, "result": text(&tool["result"])}),
        Err(refusal) => refusal,
    })
}

/// One tool row's file edits with their before and after texts.
pub async fn load_tool_file_edits(api: Arc<Api>, args: Fields) -> Result<Value, String> {
    const MISSING: &str = "No file edit in that row";
    let tool = match sidebar(&api).await?.tool(&args, MISSING).await? {
        Ok(tool) => tool,
        Err(refusal) => return Ok(refusal),
    };
    let mut edits: Vec<Value> = tool["fileEdits"].as_array().into_iter().flatten().filter(|edit| edit.is_object()).cloned().collect();
    if edits.is_empty() && tool["fileEdit"].is_object() {
        edits.push(tool["fileEdit"].clone());
    }
    Ok(if edits.is_empty() { refused(MISSING) } else { json!({"ok": true, "fileEdits": edits}) })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_turn_becomes_its_rows_and_big_results_and_edits_are_cut() {
        let turn = json!({"role": "assistant", "content": "", "incomplete": true, "error": " ", "blocks": [
            {"type": "thinking", "text": "hm"}, {"type": "text", "text": "  "}, "junk",
            {"type": "tool_call", "id": 7, "name": "Write", "arguments": {"path": "é".repeat(200)}, "status": "success",
             "duration_ms": 12.9, "result": {"data": {"n": null}, "exit_code": 0}, "file_edits": [{"before": "a".repeat(4001), "after": ""}]},
            {"type": "user", "text": "wait"},
        ]});
        let mut rows = convert(2, &turn);
        let ids: Vec<i64> = rows.iter().map(|row| row["id"].as_i64().unwrap() - 2 * ROW_ID_STEP).collect();
        assert_eq!(ids, [0, 1, 2, 3, 4]);
        let call = rows[1]["text"].as_str().unwrap();
        assert!(call.starts_with("⚙ Write({\"path\": \"é") && call.ends_with("...)") && call.chars().count() == "⚙ Write()".chars().count() + 120);
        assert_eq!(rows[2]["text"], format!("{call} · success · 12ms"));
        assert_eq!((&rows[2]["role"], &rows[2]["tool"]["result"], &rows[2]["tool"]["exitCode"]), (&json!("success"), &json!("{'n': None}"), &json!(0)));
        assert_eq!(rows[4], json!({"id": 2 * ROW_ID_STEP + 4, "role": "assistant", "text": "", "incomplete": true, "checkpoint": true, "run_id": null, "live_run": false}));
        trim_edits(&mut rows[2]["tool"]);
        assert_eq!(rows[2]["tool"]["fileEdit"], json!({"before": "", "after": "", "contentOmitted": true}));
        assert_eq!(rows[2]["tool"]["fileEdits"][0], rows[2]["tool"]["fileEdit"]);

        let mut tool = json!({"result": "é".repeat(TOOL_RESULT_CAP + 1)});
        cap_result(&mut tool);
        assert_eq!((tool["result"].as_str().unwrap().chars().count(), &tool["resultBytes"]), (TOOL_RESULT_CAP, &json!(2 * TOOL_RESULT_CAP + 2)));
        assert_eq!(convert(0, &json!({"content": "hi"})), [json!({"id": 0, "role": "user", "text": "hi"})]);
        assert_eq!(convert(0, &json!({"role": "system", "content": "hi"})), Vec::<Value>::new());
    }
}
