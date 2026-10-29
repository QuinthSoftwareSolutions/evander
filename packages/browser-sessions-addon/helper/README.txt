Evander Browser Helper 0.3.0

Windows: extract this ZIP and run Install.cmd. Paste the extension ID shown under
"Install the optional helper" in the Evander Chrome sidebar. Installation is per-user
and requires no separate .NET runtime or administrator access. End active helper
sharing before reinstalling. Remove it through Windows Installed Apps.

Linux/macOS: run chmod +x evander-browser-helper, then:
  ./evander-browser-helper --install YOUR_EXTENSION_ID
Remove it with: evander-browser-helper --uninstall

If Chrome uses a non-default user-data directory, append to the install command:
  --browser-data-dir "/path/from/chrome-version/profile-parent"
Use the parent of the Profile Path shown at chrome://version (the directory that
contains Local State and DevToolsActivePort, rather than Default/Profile 1).

In Chrome 144 or newer, open chrome://inspect/#remote-debugging and enable remote
debugging. Select "Installed helper" in the sidebar before pairing a fresh invitation.
Approve Chrome's local debugging connection prompt. Sharing still starts observe-only.

Only the registered Evander extension may start this helper. It connects to Chrome
on loopback using DevToolsActivePort and binds one selected page target. The helper
receives neither Evander host tokens nor voice-provider credentials, and exposes no
TCP listener. No remote-debugging URL should be published or forwarded to the internet.

Production distributors should sign the executable/package using their normal release
signing process. The repository build does not contain a signing certificate.
