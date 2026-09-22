# Open UEFN, private version, memory calculation

UEFN has no API for these menus. Drive them with captures and clicks.
`nx` / `ny` are 0..1 from the top-left of that window's image.

Tools: `ducky_close_uefn`, `ducky_launch_uefn`, `ducky_restart_uefn`,
`ducky_wait_uefn_ready`, `uefn_window_list`, `uefn_window_capture`,
`uefn_window_click`, `uefn_wait_window`, `clipboard_get_text`,
`ducky_publish_private_version`.

There are two separate Project-menu commands. Do the one that was asked for.

- **Upload to Private Version...** — team private build. Ends at a code popup.
- **Launch Memory Calculation** — memory budget on the Fortnite client. It can
  show that same code popup in the middle; the client does not start until OK
  is pressed.

**Publish Project...** is a different submenu. Do not use it for either flow.

## Open and close the editor

A bare `.uefnproject` path does not open the island. Launch with
`ducky_launch_uefn` (it passes `-ValkyrieProject=<path>`). Empty `project`
uses the panel's current island.

- UEFN is closed: `ducky_launch_uefn`, then `ducky_wait_uefn_ready`.
- UEFN is open on the wrong island, or the listener is down after a real
  reopen was requested: `ducky_restart_uefn`. That closes (Save if asked,
  force-kill only if it is still up), opens the project, and waits until the
  listener matches.
- Close only: `ducky_close_uefn`.

Do not restart the editor to refresh a stale listener or to delete assets.
That case is `reload_listener` once, then stay on `workspace_*`.

## Session target — required before memory calculation

The control is the dropdown on the **Launch Session** button, not the button
itself. Its Platform section has:

- **Launch on this PC** (filled dot when selected)
- **Mobile Preview (This PC)**
- **Connect To Platform**

Memory calculation launches Fortnite on this PC. If the filled dot is not on
**Launch on this PC**, click that row before **Launch Memory Calculation**.
Leave the user's choice alone when they only asked for a private version.

1. Capture the main editor window.
2. Click the Launch Session dropdown (the arrow beside Launch Session).
3. Capture again. If **Launch on this PC** is not the filled dot, click it.
4. Click the menu away (empty editor space) if it stays open.

## Upload to Private Version

1. Capture the main editor window. Click **Project**.
2. Capture again. Click **Upload to Private Version...**
   (tooltip: create a private version to share with team members).
3. Wait for the popup titled `Private version has been created!`.
4. Copy the code, send it to the user, then press **OK** so the popup closes.
   See "Code popup" below.

## Launch Memory Calculation

1. Do "Session target" first so the filled dot is **Launch on this PC**.
2. Capture the main editor window. Click **Project**.
3. Capture again. Click **Launch Memory Calculation**
   (tooltip: launch a memory calculation on the client).
4. If `Private version has been created!` appears: copy the code, send it to
   the user, press **OK**. The calculation does not continue while that popup
   is open.
5. Fortnite then launches on this PC and runs the memory calculation. Report
   the budget result from the editor when it comes back. Do not invent numbers.

## Code popup

Title is `Private version has been created!`. Buttons: **Copy**, and **OK**
to dismiss. **Show In Creator Portal** may also be there — leave it unless
asked.

Prefer `ducky_publish_private_version`. It waits for that title, captures the
popup, clicks Copy when a spot is cached for this dialog size, and clicks OK
when that spot is cached too.

No cache yet:

1. `uefn_window_capture(title_regex="Private version has been created")`.
2. From the image, call
   `ducky_publish_private_version(copy_nx=…, copy_ny=…, done_nx=…, done_ny=…)`
   where `done_*` is the **OK** button.
3. A clipboard value matching `1234-5678-9012` (optional `?v=N`) stores those
   spots. Later runs click Copy and OK without a new image.

Post `code` in the chat. If `code` is empty, post the capture path and the
error. Never invent a code.

## Stop and report these instead of clicking through

- Moderation issues (Creator Portal)
- Experimental features blocking publish
- Login required
- A build already in progress
