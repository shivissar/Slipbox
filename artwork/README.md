# Slipbox artwork

| File | What it is |
| --- | --- |
| `slipbox-icon.svg` | App icon: the mascot waving, on light blue (vector, full-bleed 1024). |
| `slipbox-icon-1024.png` | The app icon on Apple's grid (824-px square, soft shadow, transparent margin). |
| `slipbox-icon-1024-full-bleed.png` | The app icon filling the whole square, for the web and documents. |
| `slipbox-disk-icon.svg` | Disk-image icon: the mascot in its slip box (vector, full-bleed 1024). |
| `slipbox-disk-icon-1024.png` | The disk-image icon on Apple's grid. |
| `slipbox-disk-icon-1024-full-bleed.png` | The disk-image icon filling the whole square. |
| `slipbox-logo-tm.png` | The logo with a small ™ just outside its top-right corner (1024), for the README and release notes; a 512 copy is the About panel's logo, `src/Slipbox/resources/about-logo.png`. |
| `Slipbox-installer.icns` | The disk-image icon at every size; `installer_icon` in pyproject.toml points here. |

The app's own `.icns` is `src/Slipbox/resources/Slipbox.icns`. The mascot is
the one drawn in `src/Slipbox/templates/_ui.html`. How the icons are built:
DEVNOTES.md, "Icons".
