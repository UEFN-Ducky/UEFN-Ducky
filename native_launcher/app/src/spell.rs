//! Windows' own spell checker (ISpellChecker, the engine behind WebView2's red underlines):
//! fixes for one word, and Add to dictionary. The launcher's twin of the backend's
//! `frontend/ui_web/win_spellcheck.py`, for the right-click menu over typed text.

use windows::core::{HSTRING, PWSTR};
use windows::Win32::Globalization::{
    GetUserDefaultLocaleName, ISpellChecker, ISpellCheckerFactory, ISpellingError, SpellCheckerFactory,
};
use windows::Win32::System::Com::{CoCreateInstance, CoTaskMemFree, CLSCTX_INPROC_SERVER};

/// At most this many fixes, as the backend's menu shows.
const MAX: usize = 5;

/// The checker for the person's language (en-US when Windows has none for it). COM is set up
/// on the calling thread: the UI thread, which WebView2 needs it on too.
unsafe fn checker() -> windows::core::Result<ISpellChecker> {
    let factory: ISpellCheckerFactory = CoCreateInstance(&SpellCheckerFactory, None, CLSCTX_INPROC_SERVER)?;
    let mut name = [0u16; 85];
    let n = GetUserDefaultLocaleName(&mut name);
    let own = if n > 1 { String::from_utf16_lossy(&name[..n as usize - 1]) } else { String::new() };
    if !own.is_empty() && own != "en-US" {
        if let Ok(found) = factory.CreateSpellChecker(&HSTRING::from(own)) {
            return Ok(found);
        }
    }
    factory.CreateSpellChecker(&HSTRING::from("en-US"))
}

/// Fixes for a misspelled `word`; `None` when it is spelled right or nothing can tell.
pub(crate) fn fixes(word: &str) -> Option<Vec<String>> {
    if word.is_empty() {
        return None;
    }
    unsafe {
        let checker = checker().ok()?;
        let word = HSTRING::from(word);
        let errors = checker.Check(&word).ok()?;
        let mut first: Option<ISpellingError> = None;
        // S_FALSE (1) with nothing in `first`: no error in the word.
        if errors.Next(&mut first).0 != 0 || first.is_none() {
            return None;
        }
        let found = checker.Suggest(&word).ok()?;
        let mut out = Vec::new();
        while out.len() < MAX {
            let mut one = [PWSTR::null()];
            let mut got = 0u32;
            if found.Next(&mut one, Some(&mut got)).0 != 0 || got == 0 || one[0].is_null() {
                break;
            }
            let fix = one[0].to_string().unwrap_or_default();
            CoTaskMemFree(Some(one[0].0 as _));
            if !fix.is_empty() {
                out.push(fix);
            }
        }
        Some(out)
    }
}

/// Add to dictionary: the person's Windows dictionary, so the underline goes too.
pub(crate) fn add_word(word: &str) -> bool {
    !word.is_empty() && unsafe { checker().and_then(|c| c.Add(&HSTRING::from(word))).is_ok() }
}

#[cfg(test)]
mod tests {
    use super::*;
    use windows::Win32::System::Com::{CoInitializeEx, COINIT_APARTMENTTHREADED};

    #[test]
    fn nothing_to_fix_in_an_empty_or_a_right_word() {
        unsafe {
            let _ = CoInitializeEx(None, COINIT_APARTMENTTHREADED);
        }
        assert_eq!(fixes(""), None);
        assert!(!add_word(""));
        // Where Windows has a checker, a word spelled right has no fixes; where it has none,
        // nothing can tell. Either way: no spelling rows.
        assert_eq!(fixes("hello"), None);
    }

    #[test]
    fn a_misspelled_word_gets_at_most_five_fixes_where_windows_can_check() {
        unsafe {
            let _ = CoInitializeEx(None, COINIT_APARTMENTTHREADED);
            if checker().is_err() {
                return; // no spell checker on this PC: the menu simply has no spelling rows
            }
        }
        let found = fixes("recieve").expect("a misspelled word");
        assert!(found.len() <= MAX);
        assert!(found.iter().any(|f| f == "receive"), "{found:?}");
    }
}
