//! The panel's calls (`/__panel_api/<method>`) and the events its windows poll.
//!
//! [`methods`] is the route table: a method is answered here once it is in it, and by the
//! Python backend until then.

pub mod push;
pub mod router;
pub mod settings;
pub mod shared;

use std::collections::HashMap;

pub use router::{Api, Method, Reach};

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
