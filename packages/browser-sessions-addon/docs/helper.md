# Optional local browser helper

The helper preserves native browser input and Chrome accessibility/screencasting when another extension prevents `chrome.debugger` attachment. Chat, optional voice, origin approval and stop controls remain in the Chrome sidebar. The extension-only mode remains available without a companion installation. Hosted sessions already use native worker-side CDP and do not need this helper.

## Install and connect

Use Chrome 144 or newer. Open the extension's **Install the optional helper** instructions and copy its extension ID. A pasted invitation exposes the control plane's `/_browser/helper` download page; it lists only packages the operator has published.

On Windows, extract the Windows package and run `Install.cmd`. Paste the extension ID when prompted. The per-user installer registers `com.evander.browser_helper` under HKCU and adds an Installed Apps removal entry. No administrator permission, Node.js, .NET runtime or Windows service is required. The executable and its profile configuration live under `%LOCALAPPDATA%\Evander\BrowserHelper`. End active helper sharing before reinstalling.

On Linux/macOS, extract the corresponding package, make `evander-browser-helper` executable and run:

```sh
./evander-browser-helper --install EXTENSION_ID
```

Each installation authorizes the exact extension ID; there is no wildcard origin. Rerun installation if an unpacked extension moves and receives another ID. The installer retains explicitly registered IDs for other local Chrome profiles. Remove the helper through Windows Installed Apps or `evander-browser-helper --uninstall`. End active sharing before removal.

Open `chrome://inspect/#remote-debugging` and enable Chrome's remote debugging connection. Choose **Installed helper — full browser control** in the extension, then pair a fresh invitation. Chrome asks permission for the local debugging connection. Approve it to continue; the Truman session still starts observe-only. Use **Allow Truman to interact** to grant input.

The default is the stable Chrome user-data directory. For another channel or a custom profile directory, append `--browser-data-dir "/path/to/user-data"` to the install command. Use the parent of **Profile Path** from `chrome://version`, containing `Local State` and `DevToolsActivePort`, rather than its `Default` or `Profile 1` subdirectory. Selecting another tab or profile with the same URL cannot redirect the helper: the extension supplies the selected tab's exact Chrome target ID, which the helper verifies.

## Boundaries and lifecycle

Chrome starts the native host on demand over framed stdin/stdout. The host validates the calling extension origin against its installed configuration. It reads `DevToolsActivePort` from the configured local profile and connects only to `ws://127.0.0.1`; it opens no TCP listener, publishes no debugging port and launches no replacement Chrome/profile. Chrome's consent UI controls the connection. Do not forward its local debugging endpoint externally.

The helper independently pins one page target, owner, approved origin, control generation, document epoch and expiry. It accepts a fixed set of CDP operations needed by the shared engine; arbitrary evaluation, cookies, network interception and file APIs are rejected. State revocations bypass the command queue. Navigation/origin changes invalidate pending operations; no mutation is replayed. It never receives relay/host capability tokens or speech-provider credentials.

Closing the native port disconnects from Chrome without closing the user's browser. Browser disconnects, session expiry and removal of the installed configuration revoke helper access. A helper failure ends sharing; select extension-only mode with a fresh invitation if the companion is unavailable. Enterprise remote-debugging policy and Chrome's consent prompt remain authoritative. The extension's normal and compatibility modes keep their existing restrictions.

## Build and publish

A .NET 10 SDK is needed only to build the helper. From this add-on directory:

```sh
python scripts/package_helper.py --runtime win-x64
# Optional additional targets: win-arm64, linux-x64, linux-arm64, osx-x64, osx-arm64.
```

This produces a compressed self-contained executable, installer instructions and `helper-<runtime>.zip` under `evander_browser_sessions_addon/ui_static`. Build the desired helper packages **before building the Python wheel or deployment bundle**; package data includes published ZIPs. Binaries are ignored release artifacts and are not fetched or compiled on a production host at startup. A deployment that omits them keeps extension-only sharing usable and reports that helper packages have not been published. The download endpoint rejects unknown filenames and missing packages.

Use the distributor's normal executable/package signing process for production Windows/macOS releases. The repository contains no signing certificate. The Windows package can be cross-published; native installation and Chrome consent UI must also be qualified on each supported operating system before general distribution.

Run `npm run test:helper` with `EVANDER_TEST_PYTHON` set to the repository interpreter. It tests real Chrome Native Messaging and CDP in disposable profiles, including a protected frame from a second extension, trusted input, local grants and origin approval. The fixture's explicit debugging flag supplies the endpoint without automating Chrome's native consent prompt; it is never added by the shipped helper. The ordinary browser suite continues to require no .NET SDK.
