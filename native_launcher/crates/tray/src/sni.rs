//! Linux tray: a StatusNotifierItem over D-Bus (`ksni`): KDE, XFCE, most panels, GNOME with
//! the AppIndicator extension. The same menu as Windows ([`crate::MENU`]). Without a tray
//! host [`spawn`] says [`NoTray`], and closing the window minimises instead of hiding it.

use crate::{default_action, MenuAction, DEFAULT_TITLE, ICON_NAME, MENU};

/// The icon the Linux package installs in the hicolor theme (`uefn-ducky.png`).
pub const THEME_ICON: &str = "uefn-ducky";

/// The menu, in order, as `(label, action)`.
pub fn menu_items() -> Vec<(&'static str, MenuAction)> {
    MENU.iter().map(|i| (i.label, i.action)).collect()
}

/// What closing the main window does.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CloseAction {
    /// Hide it; the tray brings it back.
    HideToTray,
    /// No tray to bring it back from: minimise.
    Minimise,
}

pub fn close_action(tray_shown: bool) -> CloseAction {
    if tray_shown {
        CloseAction::HideToTray
    } else {
        CloseAction::Minimise
    }
}

/// No StatusNotifierWatcher on the session bus (or no session bus).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct NoTray(pub String);

struct DuckyTray<F> {
    on: F,
}

impl<F: Fn(MenuAction) + Send + 'static> ksni::Tray for DuckyTray<F> {
    fn id(&self) -> String {
        ICON_NAME.into()
    }
    fn title(&self) -> String {
        DEFAULT_TITLE.into()
    }
    fn icon_name(&self) -> String {
        THEME_ICON.into()
    }
    fn activate(&mut self, _x: i32, _y: i32) {
        (self.on)(default_action())
    }
    fn menu(&self) -> Vec<ksni::MenuItem<Self>> {
        menu_items()
            .into_iter()
            .map(|(label, action)| {
                ksni::menu::StandardItem { label: label.into(), activate: Box::new(move |t: &mut Self| (t.on)(action)), ..Default::default() }
                    .into()
            })
            .collect()
    }
}

/// Live while the tray icon is shown.
pub struct TrayHandle(#[allow(dead_code)] ksni::blocking::Handle<DuckyTray<Box<dyn Fn(MenuAction) + Send>>>);

/// Show the tray icon; `on` runs on ksni's thread for each menu pick or click.
pub fn spawn(on: impl Fn(MenuAction) + Send + 'static) -> Result<TrayHandle, NoTray> {
    use ksni::blocking::TrayMethods;
    let on: Box<dyn Fn(MenuAction) + Send> = Box::new(on);
    DuckyTray { on }.spawn().map(TrayHandle).map_err(|e| NoTray(e.to_string()))
}
