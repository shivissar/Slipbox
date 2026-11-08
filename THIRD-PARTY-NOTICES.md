# Third-party components in Verweis

Verweis's own code is under the PolyForm Noncommercial License 1.0.0
(see LICENSE). The macOS app also ships the following, each under its own
licence. Those licences apply to those components, not to Verweis.

## Programs, fonts and scripts

| Component | Version | Licence | Where in the app |
|---|---|---|---|
| Pandoc | 3.1.13 | GPL-2.0-or-later | `resources/bin/pandoc`; licence and source pointer in `resources/licenses/pandoc/` |
| HTMX | 1.9.12 | 0BSD | `static/vendor/` (with `HTMX-LICENSE.txt`) |
| Nunito, Zen Maru Gothic, Atkinson Hyperlegible, Literata | see font folders | SIL Open Font License 1.1 | `static/fonts/*/` (each with `OFL.txt`) |
| Python | 3.13 | PSF-2.0 | the Python runtime Briefcase builds into the app, with the licences of the libraries it includes |

## Python packages that need more than a mention

| Package | Version | Licence | Notes |
|---|---|---|---|
| certifi | 2026.10.23 | MPL-2.0 | Root certificates for HTTPS. Shipped unmodified, as source, in `app_packages/certifi/`. Source: https://github.com/certifi/python-certifi |
| numpy | 2.5.4 | BSD-3-Clause (and others for bundled parts) | Its wheels bundle OpenBLAS (BSD-3-Clause), LAPACK (BSD-3-Clause-Open-MPI), the GCC runtime library (GPL-3.0-or-later WITH GCC-exception-3.1) and libquadmath (LGPL-2.1-or-later, a separate dynamic library in `numpy/.dylibs/`, source at https://gcc.gnu.org/git/?p=gcc.git;a=tree;f=libquadmath). Full texts: `numpy-*.dist-info/licenses/LICENSE.txt` in `app_packages/`. |
| cryptography | 48.0.1 | Apache-2.0 OR BSD-3-Clause | Includes a statically linked OpenSSL (Apache-2.0). Used for locked notes. |

## Other Python packages

Each ships with its own licence file in its `*.dist-info` folder inside
`app_packages/`. Versions are those resolved for Verweis 5.2.0 on macOS;
a given build may differ slightly (the exact set is in `app_packages/`).

| Package | Version | Licence |
|---|---|---|
| annotated-types | 0.8.0 | MIT |
| anthropic | 2.6.0 | Apache-2.0 |
| anyio | 4.16.0 | MIT |
| bleach | 6.4.0 | Apache-2.0 |
| blinker | 1.9.0 | MIT |
| cffi | 2.1.1 | MIT-0 |
| charset-normalizer | 3.5.2 | MIT |
| click | 8.5.1 | BSD-3-Clause |
| distro | 1.9.0 | Apache-2.0 |
| docstring_parser | 0.18.0 | MIT |
| Flask | 3.1.3 | BSD-3-Clause |
| fonttools | 4.68.0 | MIT |
| google-auth | 2.62.1 | Apache-2.0 |
| google-genai | 3.0.0 | Apache-2.0 |
| h11 | 0.16.0 | MIT |
| httpcore2 | 2.16.0 | BSD-3-Clause |
| httpx2 | 2.16.0 | BSD-3-Clause |
| idna | 3.20 | BSD-3-Clause |
| itsdangerous | 2.2.0 | BSD |
| Jinja2 | 3.1.6 | BSD |
| jiter | 0.19.0 | MIT |
| Markdown | 3.11.3 | BSD-3-Clause |
| MarkupSafe | 3.0.4 | BSD-3-Clause |
| openai | 3.46.0 | Apache-2.0 |
| opentelemetry-api | 1.45.2 | Apache-2.0 |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause |
| platformdirs | 4.14.5 | MIT |
| pyasn1 | 0.6.5 | BSD-2-Clause |
| pyasn1_modules | 0.4.2 | BSD |
| pycparser | 3.12 | BSD-3-Clause |
| pydantic | 2.14.3 | MIT |
| pydantic_core | 2.50.4 | MIT |
| Pygments | 2.21.0 | BSD-2-Clause |
| python-frontmatter | 1.3.0 | MIT |
| PyYAML | 6.0.3 | MIT |
| requests | 2.34.2 | Apache-2.0 |
| rubicon-objc | 0.5.7 | BSD-3-Clause |
| sniffio | 1.3.1 | MIT OR Apache-2.0 |
| std-nslog | 1.0.3 | BSD-3-Clause |
| tenacity | 9.1.4 | Apache-2.0 |
| toga-cocoa | 0.5.7 | BSD-3-Clause |
| toga-core | 0.5.7 | BSD-3-Clause |
| travertino | 0.5.7 | BSD-3-Clause |
| truststore | 0.10.5 | MIT |
| typing-inspection | 0.4.5 | MIT |
| typing_extensions | 4.16.0 | PSF-2.0 |
| urllib3 | 2.8.0 | MIT |
| watchdog | 7.1.0 | Apache-2.0 |
| webencodings | 0.6.1 | BSD |
| websockets | 17.4 | BSD-3-Clause |
| Werkzeug | 3.1.9 | BSD-3-Clause |

## Distributing the app

If you redistribute the app publicly (for example on GitHub Releases), the
cautious way to meet the copyleft licences is to offer the source from the
same place as the app:

- **Pandoc (GPL-2.0-or-later):** attach pandoc's 3.1.13 source archive to
  the same release.
- **libquadmath (LGPL-2.1-or-later), inside numpy:** attach the matching
  libquadmath source from GCC to the same release as well, or replace
  numpy (it is only used to compare notes for "related notes").
- **certifi (MPL-2.0):** it ships as unmodified Python source, so the app
  already contains its source; keep its licence file with it.
- Keep the `*.dist-info` licence files and this notice with the app.
