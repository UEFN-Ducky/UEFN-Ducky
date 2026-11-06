//! IFileOpenDialog / IFileSaveDialog.

use windows::core::{HSTRING, PCWSTR, PWSTR};
use windows::Win32::Foundation::{ERROR_CANCELLED, HWND};
use windows::Win32::System::Com::{
    CoCreateInstance, CoInitializeEx, CoTaskMemFree, CoUninitialize, CLSCTX_INPROC_SERVER,
    COINIT_APARTMENTTHREADED,
};
use windows::Win32::UI::Shell::Common::COMDLG_FILTERSPEC;
use windows::Win32::UI::Shell::{
    FileOpenDialog, FileSaveDialog, IFileDialog, IFileOpenDialog, IFileSaveDialog, IShellItem,
    SHCreateItemFromParsingName, FOS_ALLOWMULTISELECT, FOS_FILEMUSTEXIST, FOS_FORCEFILESYSTEM,
    FOS_OVERWRITEPROMPT, FOS_PATHMUSTEXIST, FOS_PICKFOLDERS, SIGDN_FILESYSPATH,
};

use crate::{DialogKind, DialogRequest};

struct ComGuard(bool);
impl Drop for ComGuard {
    fn drop(&mut self) {
        if self.0 {
            unsafe { CoUninitialize() };
        }
    }
}

fn err(e: windows::core::Error) -> String {
    e.message().to_string()
}

fn is_cancel(e: &windows::core::Error) -> bool {
    e.code() == ERROR_CANCELLED.to_hresult()
}

unsafe fn item_path(item: &IShellItem) -> Result<String, String> {
    let p: PWSTR = item.GetDisplayName(SIGDN_FILESYSPATH).map_err(err)?;
    let s = p.to_string().map_err(|e| e.to_string());
    CoTaskMemFree(Some(p.0 as *const _));
    s
}

unsafe fn prepare(dlg: &IFileDialog, req: &DialogRequest, wide: &[(Vec<u16>, Vec<u16>)]) -> Result<(), String> {
    let mut opts = dlg.GetOptions().map_err(err)? | FOS_FORCEFILESYSTEM | FOS_PATHMUSTEXIST;
    match req.kind {
        DialogKind::Open => opts |= FOS_FILEMUSTEXIST,
        DialogKind::Folder => opts |= FOS_PICKFOLDERS,
        DialogKind::Save => opts |= FOS_OVERWRITEPROMPT,
    }
    if req.allow_multiple && req.kind != DialogKind::Save {
        opts |= FOS_ALLOWMULTISELECT;
    }
    dlg.SetOptions(opts).map_err(err)?;

    if req.kind != DialogKind::Folder && !wide.is_empty() {
        let specs: Vec<COMDLG_FILTERSPEC> = wide
            .iter()
            .map(|(n, s)| COMDLG_FILTERSPEC { pszName: PCWSTR(n.as_ptr()), pszSpec: PCWSTR(s.as_ptr()) })
            .collect();
        dlg.SetFileTypes(&specs).map_err(err)?;
        dlg.SetFileTypeIndex(1).map_err(err)?;
        if req.kind == DialogKind::Save {
            if let Some(ext) = req.filters[0].default_extension() {
                dlg.SetDefaultExtension(&HSTRING::from(ext)).map_err(err)?;
            }
        }
    }
    if !req.directory.is_empty() {
        // A folder that no longer exists just keeps the system default.
        if let Ok(folder) = SHCreateItemFromParsingName::<_, _, IShellItem>(&HSTRING::from(req.directory.as_str()), None) {
            let _ = dlg.SetFolder(&folder);
        }
    }
    if req.kind == DialogKind::Save && !req.save_filename.is_empty() {
        dlg.SetFileName(&HSTRING::from(req.save_filename.as_str())).map_err(err)?;
    }
    Ok(())
}

pub fn show(owner_hwnd: isize, req: &DialogRequest) -> Result<Option<Vec<String>>, String> {
    let wide: Vec<(Vec<u16>, Vec<u16>)> = req
        .filters
        .iter()
        .map(|f| {
            let w = |s: &str| s.encode_utf16().chain(std::iter::once(0)).collect::<Vec<u16>>();
            (w(&f.name), w(&f.spec))
        })
        .collect();
    let owner = if owner_hwnd == 0 { None } else { Some(HWND(owner_hwnd as *mut _)) };
    unsafe {
        // The UI thread is normally STA already; only undo what this call set up.
        let _com = ComGuard(CoInitializeEx(None, COINIT_APARTMENTTHREADED).is_ok());
        match req.kind {
            DialogKind::Save => {
                let dlg: IFileSaveDialog = CoCreateInstance(&FileSaveDialog, None, CLSCTX_INPROC_SERVER).map_err(err)?;
                prepare(&dlg.clone().into(), req, &wide)?;
                if let Err(e) = dlg.Show(owner) {
                    return if is_cancel(&e) { Ok(None) } else { Err(err(e)) };
                }
                let item = dlg.GetResult().map_err(err)?;
                Ok(Some(vec![item_path(&item)?]))
            }
            DialogKind::Open | DialogKind::Folder => {
                let dlg: IFileOpenDialog = CoCreateInstance(&FileOpenDialog, None, CLSCTX_INPROC_SERVER).map_err(err)?;
                prepare(&dlg.clone().into(), req, &wide)?;
                if let Err(e) = dlg.Show(owner) {
                    return if is_cancel(&e) { Ok(None) } else { Err(err(e)) };
                }
                let items = dlg.GetResults().map_err(err)?;
                let n = items.GetCount().map_err(err)?;
                let mut out = Vec::with_capacity(n as usize);
                for i in 0..n {
                    out.push(item_path(&items.GetItemAt(i).map_err(err)?)?);
                }
                Ok(Some(out))
            }
        }
    }
}
