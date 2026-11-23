//! Linux splash: a borderless tao window with the duck on the theme background, shown before
//! the Python app starts and closed when the panel is ready (see [`step`]).

use std::time::{Duration, Instant};

use tao::event::Event;
use tao::event_loop::{ControlFlow, EventLoop};
use tao::platform::run_return::EventLoopExtRunReturn;
use tao::platform::unix::WindowExtUnix;
use tao::window::WindowBuilder;

use crate::{step, Splash, Step};

/// Ducky's icon, the one the Windows EXE carries.
const DUCK_ICO: &[u8] = include_bytes!("../../../../build/app_icon.ico");
const SIZE: u32 = 280;
const DUCK_PX: i32 = 160;
const POLL: Duration = Duration::from_millis(100);

fn duck() -> Option<gtk::Image> {
    use gtk::gdk_pixbuf::{InterpType, PixbufLoader};
    use gtk::prelude::*;
    let loader = PixbufLoader::new();
    loader.write(DUCK_ICO).ok()?;
    loader.close().ok()?;
    let pix = loader.pixbuf()?.scale_simple(DUCK_PX, DUCK_PX, InterpType::Bilinear)?;
    Some(gtk::Image::from_pixbuf(Some(&pix)))
}

/// Show `splash` until `ready()` (polled every 100 ms) or `cap`. Returns at once when the
/// splash is hidden (a `--background` start) or there is no display.
pub fn show_until(mut splash: Splash, mut ready: impl FnMut() -> bool, cap: Duration) {
    if !splash.visible() || gtk::init().is_err() {
        return;
    }
    use gtk::prelude::*;
    let mut el = EventLoop::new();
    let (r, g, b) = crate::BACKGROUND_RGB;
    let Ok(win) = WindowBuilder::new()
        .with_title("UEFN Ducky")
        .with_decorations(false)
        .with_resizable(false)
        .with_always_on_top(true)
        .with_inner_size(tao::dpi::LogicalSize::new(SIZE, SIZE))
        .with_background_color((r, g, b, 255))
        .build(&el)
    else {
        return;
    };
    if let (Some(vbox), Some(img)) = (win.default_vbox(), duck()) {
        vbox.pack_start(&img, true, true, 0);
        vbox.show_all();
    }
    if let Some(m) = win.current_monitor() {
        let (ms, ws) = (m.size(), win.outer_size());
        let p = m.position();
        win.set_outer_position(tao::dpi::PhysicalPosition::new(
            p.x + (ms.width as i32 - ws.width as i32) / 2,
            p.y + (ms.height as i32 - ws.height as i32) / 2,
        ));
    }
    let started = Instant::now();
    el.run_return(|ev, _, cf| {
        if matches!(ev, Event::NewEvents(_) | Event::MainEventsCleared) {
            match step(&mut splash, ready(), started.elapsed(), cap) {
                Step::Wait => *cf = ControlFlow::WaitUntil(Instant::now() + POLL),
                Step::Close => *cf = ControlFlow::Exit,
            }
        }
    });
    drop(win);
}
