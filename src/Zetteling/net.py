"""Certificates for outbound HTTPS (5.2).

Briefcase's bundled Python doesn't always find the macOS root certificates,
so a plain urlopen to an https address can fail with CERTIFICATE_VERIFY_FAILED
in the packaged app while working from a terminal. certifi's bundle (already
installed for the AI libraries) always works."""

# Copyright (c) 2026 Shiv Issar
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

import ssl

_CONTEXT = []


def ssl_context():
    if not _CONTEXT:
        try:
            import certifi
            _CONTEXT.append(ssl.create_default_context(cafile=certifi.where()))
        except Exception:
            _CONTEXT.append(ssl.create_default_context())
    return _CONTEXT[0]
