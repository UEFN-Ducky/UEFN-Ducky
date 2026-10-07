# Installed panel integrity and recovery

An affected 1.2.347 installation reported missing assets/index-Cu3cgvcC.js.
The published Setup SHA256 was verified and its Inno archive contained that
exact asset. The reason it disappeared from affected installed copies remains
unproven; do not attribute it to antivirus or an installer omission without evidence.

The build takes a private snapshot of the full Vite dist and creates
frontend/ui_web/panel-dist.zip from that same snapshot. A shared snapshot could
be replaced by another build, so every build owns a TemporaryDirectory.
Build and frozen runtime gates compare every asset's size and CRC to the archive,
including lazy chunks, CSS, workers and public files, rather than only index refs.

Before serving the packaged UI, panel_assets.ensure_panel_dist checks every
installed asset. A missing or damaged file causes the entire matching release's
panel to be extracted to a private process-lifetime temp directory. The original
install is untouched, including under Program Files. The recovered root is reused
by that process and cleaned on exit. Unsafe archive names and corrupt archives
fail closed. Source checkouts retain the normal Vite dist validation.

Setup runs UEFN-Ducky.exe --verify-panel <report.json> after copying files and
before launching the application. This strict mode never accepts recovery as a
successful installation, starts no windows and reads no chats or settings.
An incomplete install stops Setup with an error instead of reporting success.

The saved UEFN Ducky Release workflow runs release/verify_installer_panel.py
between Build and Upload. Its innoextract argument must support the Inno version
used by Setup. This extracts the actual compiled payload without running Setup
or registering an installation. It checks the frozen runtime, then removes the
entrypoint, a lazy chunk and index.html, and corrupts an entrypoint without
changing its size. Strict installation checks must reject every damaged case;
the frozen app's actual web-root selection must recover each case and leave the
installed files untouched. Unit tests additionally reject unsafe archive names.
