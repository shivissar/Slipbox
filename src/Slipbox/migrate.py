"""Moving data across when the app was renamed from Zetteling to Slipbox
(5.2). The app's internal ID changed from com.shivissar.zetteling to
com.shivissar.slipbox, which moves its data folder in
~/Library/Application Support. The first time Slipbox starts, it moves the
old folder's contents (settings, search index, note history, and the notes
too if they were kept in the default place) into the new one."""

# Copyright (c) 2026 Shiv Issar
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

import json
import os
import shutil
from pathlib import Path

# Newest first: development builds were briefly called Verweis.
OLD_APP_IDS = ("com.shivissar.verweis", "com.shivissar.zetteling")


def _has_data(folder):
    return (folder / "settings.json").exists() or (folder / "zettel.db").exists()


def move_old_app_data(data_dir):
    """Returns the old folder if its contents were moved, else None. Never
    overwrites anything already in the new folder except empty folders."""
    data_dir = Path(data_dir)
    if _has_data(data_dir):
        return None
    for old_id in OLD_APP_IDS:
        old = data_dir.parent / old_id
        if old == data_dir or not old.is_dir() or not _has_data(old):
            continue
        for child in sorted(old.iterdir()):
            target = data_dir / child.name
            if target.exists():
                if target.is_dir() and not any(target.iterdir()):
                    target.rmdir()  # an empty notes/ or exports/ from a first launch
                else:
                    continue
            shutil.move(str(child), str(target))
        _repoint_settings(data_dir, old)
        try:
            old.rmdir()  # only if everything moved
        except OSError:
            pass
        print("[migrate] moved the app's data from %s" % old.name)
        return old
    return None


def _repoint_settings(data_dir, old):
    """Settings that named a place inside the old folder (the default notes
    folder, say) now name the same place in the new one."""
    old_s = str(old)
    for name in ("settings.json", "settings.json.bak"):
        path = data_dir / name
        try:
            stored = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(stored, dict):
            continue
        changed = False
        for key, value in stored.items():
            if not isinstance(value, str):
                continue
            full = os.path.expanduser(value)
            if full == old_s or full.startswith(old_s + os.sep):
                stored[key] = str(data_dir) + full[len(old_s):]
                changed = True
        if changed:
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(json.dumps(stored, indent=2), encoding="utf-8")
            os.replace(tmp, path)
