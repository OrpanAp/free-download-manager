# Building Zephyr-Setup.exe

One-time setup (each only needed once, ever, on your build PC):
1. Run `build_exe.bat` in the folder above this one, if you haven't already (makes `release\Zephyr.exe`).
2. Install Inno Setup (free): https://jrsoftware.org/isdl.php — use the default options.
3. Run `installer\build_installer.bat`.

That produces `installer\Output\Zephyr-Setup.exe`. Anyone who runs that one file gets:
- Zephyr.exe copied to Program Files, with a Start Menu entry and a normal uninstaller.
- A shortcuts page with checkboxes for: a desktop shortcut (ticked by default), starting Zephyr
  minimized to the tray when Windows starts, and a right-click "Send to Zephyr" entry — pick whichever.
- The browser extension copied alongside it, registered with Chrome and Edge so there's no "Load unpacked" step.

## About the browser extension (two modes)

### Mode 1 - default (no store ID): guided setup for every browser
After installing, `Install-Guide.html` opens automatically in the default browser (checked on the last installer page; also in the
Start Menu as "Installation guide"). It shows the extension folder path with a Copy button and step-by-step manual instructions for
Chrome, Edge, Brave, Vivaldi/Opera and Firefox, plus troubleshooting - for when the helper below fails.
On the last page the installer offers **"Add the Zephyr extension to my browsers (2 clicks each)"**
(also in the Start Menu as "Install browser extension"). `install_extension.ps1` finds Chrome, Edge, Brave
and Vivaldi on the PC and, for each one: copies the folder path, opens the extensions page, waits for you to
click **Load unpacked**, then pastes the path and presses Enter for you. You do: turn on *Developer mode* +
click *Load unpacked*. Repeat for each browser profile you use.

Why not silent here? Chrome/Edge do not allow installing an unpacked folder or local .crx without the user
(blocked since Chrome 33; `--load-extension` removed from branded Chrome in 137; forced install of
non-store extensions only works on domain/enterprise-managed PCs).

### Mode 2 - forced install on ALL browsers, all users, all profiles (needs the store listing)
1. Zip the contents of `browser-extension\` (or use `..\store\zephyr-extension-for-stores.zip`) and upload it to the
   Chrome Web Store (one-time $5 fee) and Edge Add-ons (free).
2. Copy the extension IDs the stores give you.
3. In `setup.iss`, remove the `;` before `#define ChromeStoreId` (and `#define EdgeStoreId`) and paste the IDs.
4. Rebuild with `build_installer.bat`.
The installer now writes a machine-wide browser policy (`ExtensionInstallForcelist`) for Chrome, Edge and Brave.
The extension is installed silently, users can't remove it, and uninstalling Zephyr removes it again.
Side effect: those browsers show "Managed by your organization". Opera/Vivaldi are not covered by this
policy; Firefox needs a signed add-on (free, addons.mozilla.org, "unlisted") plus its own policy.

## About the Windows warning
Inno Setup installers still trigger SmartScreen until the publisher has reputation, same as the plain
.exe did. See the main chat answer for the two real options: a code-signing certificate, or Unblock-File
for your own personal use.
