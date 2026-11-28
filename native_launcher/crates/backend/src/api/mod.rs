//! The panel's calls (`/__panel_api/<method>`) and the events its windows poll.
//!
//! [`methods`] is the route table: a method is answered here once it is in it, and by the
//! Python backend until then.

mod chat_doc;
pub mod account;
pub mod app_info;
pub mod chats;
pub mod file_kinds;
pub mod health;
pub mod plugin_store;
pub mod project_files;
pub mod project_paths;
pub mod push;
pub mod router;
pub mod settings;
pub mod shared;
pub mod site;
pub mod usage;

use std::collections::HashMap;

pub use router::{Api, Method, Reach};

/// A random id as Python's `uuid.uuid4()` prints one.
pub(crate) fn uuid4() -> String {
    use aes_gcm::aead::rand_core::RngCore;
    let mut bytes = [0u8; 16];
    aes_gcm::aead::OsRng.fill_bytes(&mut bytes);
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    let hex: String = bytes.iter().map(|byte| format!("{byte:02x}")).collect();
    format!("{}-{}-{}-{}-{}", &hex[..8], &hex[8..12], &hex[12..16], &hex[16..20], &hex[20..])
}

/// Every method this backend answers, by its name in `api_contract.json`.
pub fn methods() -> HashMap<&'static str, Method> {
    macro_rules! table {
        ($($module:ident :: $name:ident => $reach:ident,)*) => {
            HashMap::from([$((stringify!($name), Method { run: |api, args| Box::pin($module::$name(api, args)), reach: Reach::$reach }),)*])
        };
    }
    table! {
        settings::get_settings => Anyone,
        settings::get_appearance => Anyone,
        settings::save_appearance => Anyone,
        settings::push_appearance_live => Anyone,
        settings::save_appearance_profile => Anyone,
        settings::load_appearance_profile => Anyone,
        settings::rename_appearance_profile => Anyone,
        settings::delete_appearance_profile => Anyone,
        settings::update_active_appearance_profile => Anyone,
        chats::list_folders => Anyone,
        chats::list_conversations => Anyone,
        chats::list_all_conversations => Anyone,
        chats::list_conversations_for_file => Anyone,
        chats::create_folder => Anyone,
        chats::rename_folder => Anyone,
        chats::rename_conversation => Anyone,
        chats::set_muted => Anyone,
        chats::delete_folder => Anyone,
        chats::delete_conversation => Anyone,
        chats::move_conversation => Anyone,
        chats::apply_sidebar_layout => Anyone,
        chats::group_set_leader => Anyone,
        chats::group_remove => Anyone,
        chats::load_messages => Anyone,
        chats::load_tool_result => Anyone,
        chats::load_tool_file_edits => Anyone,
        chats::list_local_chats => Anyone,
        chats::adopt_local_chats => Anyone,
        chats::delete_local_chats => Anyone,
        project_files::list_project_files => Anyone,
        project_files::list_workspace_roots => Anyone,
        project_files::list_project_file_paths => Anyone,
        project_files::read_project_file => Anyone,
        project_files::classify_project_file => Anyone,
        project_files::project_file_media_url => Anyone,
        project_files::stat_project_file => Anyone,
        project_files::fingerprint_project_dirs => Anyone,
        plugin_store::list_uefn_plugins => Anyone,
        plugin_store::duckyos_store_versions => Anyone,
        app_info::get_version => Anyone,
        app_info::get_app_patch_notes => Anyone,
        app_info::get_whats_new => Anyone,
        app_info::mark_whats_new_seen => Anyone,
        app_info::get_install_info => Anyone,
        account::duckyos_get_status => Anyone,
        account::duckyos_list_pcs => Anyone,
        account::duckyos_revoke_pc => Anyone,
        account::duckyos_login => Anyone,
        account::duckyos_cancel_login => Anyone,
        account::duckyos_open_site => Anyone,
        usage::usage_track => Anyone,
        health::ducky_health_snapshot => ThisPc,
        health::ducky_self_care_run => ThisPc,
    }
}

#[cfg(test)]
mod tests {
    #[test]
    fn every_method_here_is_in_the_contract() {
        for name in super::methods().keys() {
            assert!(super::router::in_contract(name), "{name} is not in api_contract.json");
        }
        assert!(super::router::in_contract("get_settings"));
    }
}
