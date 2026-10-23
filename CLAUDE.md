This repository revives the discontinued Ownice T3 adapter for the community. Work covers Wi-Fi,
Bluetooth, CarPlay, Android Auto, native services, video/audio, browser playback, diagnostics,
updates, and recovery on owner-authorized hardware. The default package changes web/TLS files; opt-in native HEVC packages patch the existing receiver.
HEVC playback is verified on the owner's T3 in Mac Chrome and the Tesla browser, with idle/return validation ongoing.
See README.md and docs/HEVC_VIDEO_DIAGNOSTICS.md for current deployment limits. Share reproducible
evidence and maintainable fixes so other owners can keep existing hardware useful.
Start with the project purpose, task workflow, and rules in the shared instructions below.

For each task, identify the requested adapter behavior and the relevant source files or firmware artifacts. Proceed with
source edits and local validation within that scope. Building a package does not authorize deploying
it: uploads and reboots require the owner's explicit go-ahead. Device internals are reference material,
and do not imply permission to bypass access controls or use bundled private keys.

@AGENTS.md
