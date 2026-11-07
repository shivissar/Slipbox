# Third-party components in Zetteling

Zetteling's own code is under the PolyForm Noncommercial License 1.0.0
(see LICENSE). The macOS app also ships the following, each under its own
licence. Those licences apply to those components, not to Zetteling.

| Component | Version | Licence | Where in the app |
|---|---|---|---|
| Pandoc | 3.1.13 | GPL-2.0-or-later | `resources/bin/pandoc`; licence and source pointer in `resources/licenses/pandoc/` |
| HTMX | 1.9.12 | 0BSD | `static/vendor/` (with `HTMX-LICENSE.txt`) |
| Nunito, Zen Maru Gothic, Atkinson Hyperlegible, Literata | see font folders | SIL Open Font License 1.1 | `static/fonts/*/` (each with `OFL.txt`) |
| Python and its libraries (Flask, Toga, and others) | as installed by Briefcase | various permissive licences | inside the app bundle, with each package's own licence metadata |

Pandoc source for the bundled version:
https://github.com/jgm/pandoc/releases/tag/3.1.13

If you redistribute the app publicly (for example on GitHub Releases), the
simplest way to meet the GPL's source requirement for pandoc is to attach
pandoc's 3.1.13 source archive to the same release.
