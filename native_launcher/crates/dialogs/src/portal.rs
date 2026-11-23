//! Linux dialogs through the XDG desktop portal (`rfd` with its `xdg-portal` backend): the
//! desktop's own picker (GNOME, KDE), and it works inside Flatpak. The request is turned
//! into a [`PortalCall`] first, so tests check it without a portal.

use std::path::PathBuf;

use crate::{DialogKind, DialogRequest};

/// What goes to the portal for one request.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PortalCall {
    pub kind: DialogKind,
    pub multiple: bool,
    pub directory: Option<PathBuf>,
    pub file_name: Option<String>,
    /// `(name, extensions)`; "All files" (`*.*` / `*`) adds none, the portal shows all then.
    pub filters: Vec<(String, Vec<String>)>,
}

pub fn portal_call(req: &DialogRequest) -> PortalCall {
    let filters = req
        .filters
        .iter()
        .filter_map(|f| {
            let exts: Vec<String> = f
                .spec
                .split(';')
                .filter_map(|p| p.trim().strip_prefix("*."))
                .filter(|e| !e.is_empty() && *e != "*")
                .map(str::to_string)
                .collect();
            (!exts.is_empty()).then(|| (f.name.clone(), exts))
        })
        .collect();
    PortalCall {
        kind: req.kind,
        multiple: req.allow_multiple && req.kind == DialogKind::Open,
        directory: Some(PathBuf::from(&req.directory)).filter(|d| !req.directory.is_empty() && d.is_dir()),
        file_name: Some(req.save_filename.clone()).filter(|n| !n.is_empty()),
        filters,
    }
}

/// Run `call` on `backend` (the portal, or a test's fake); picked paths as strings.
pub fn show_with(req: &DialogRequest, backend: impl FnOnce(&PortalCall) -> Option<Vec<PathBuf>>) -> Option<Vec<String>> {
    let picked = backend(&portal_call(req))?;
    Some(picked.into_iter().map(|p| p.to_string_lossy().into_owned()).collect())
}

/// The real portal.
pub fn portal(call: &PortalCall) -> Option<Vec<PathBuf>> {
    let mut d = rfd::FileDialog::new();
    if let Some(dir) = &call.directory {
        d = d.set_directory(dir);
    }
    if let Some(name) = &call.file_name {
        d = d.set_file_name(name);
    }
    for (name, exts) in &call.filters {
        d = d.add_filter(name, exts);
    }
    match (call.kind, call.multiple) {
        (DialogKind::Open, true) => d.pick_files(),
        (DialogKind::Open, false) => d.pick_file().map(|p| vec![p]),
        (DialogKind::Folder, _) => d.pick_folder().map(|p| vec![p]),
        (DialogKind::Save, _) => d.save_file().map(|p| vec![p]),
    }
}

/// A message box; true when the person picked OK / Yes.
// ponytail: the portal has no message dialog, so rfd asks zenity / kdialog here; no box
// at all without one (returns false), which only matters on a bare window manager.
pub fn message(title: &str, text: &str, ask: bool) -> bool {
    let buttons = if ask { rfd::MessageButtons::YesNo } else { rfd::MessageButtons::Ok };
    let r = rfd::MessageDialog::new().set_title(title).set_description(text).set_buttons(buttons).show();
    matches!(r, rfd::MessageDialogResult::Ok | rfd::MessageDialogResult::Yes)
}
