"""
Vega Clean Slate & Data Reset Utility.
Wipes all runtime conversation history, cached databases, memory files,
custom notes, career profile overrides, voiceprints, and logs so Vega
can start completely clean for a new user or fresh session.
"""

import os
import sys
import glob
import shutil
import argparse
from typing import Dict, Any

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def reset_all_data(interactive: bool = True, verbose: bool = True) -> Dict[str, Any]:
    """
    Safely wipes all runtime user data, chat histories, memory JSONs,
    databases, voice recordings, and debug logs.
    Preserves all codebase source code and Git-tracked files.
    """
    if interactive:
        confirm = input("Are you sure you want to completely reset all Vega memory and runtime data? (yes/no): ").strip().lower()
        if confirm not in ("yes", "y"):
            if verbose:
                print("Reset cancelled.")
            return {"status": "cancelled", "deleted": []}

    deleted = []

    # 1. Databases
    db_patterns = [
        os.path.join(BASE_DIR, "vega.db*"),
        os.path.join(BASE_DIR, "storage", "vega.db*"),
        os.path.join(BASE_DIR, "career_vault", "*.db*"),
        os.path.join(BASE_DIR, "career_vault", "jobs.db*"),
    ]
    for pat in db_patterns:
        for f in glob.glob(pat):
            try:
                os.remove(f)
                deleted.append(os.path.relpath(f, BASE_DIR))
            except Exception as e:
                if verbose:
                    print(f"Warning: could not delete {f}: {e}")

    # 2. Runtime memory and config JSONs
    json_targets = [
        "memory.json",
        "facts.json",
        "reminders.json",
        "personal.json",
        "plan.json",
        "lablog.json",
        "contacts.json",
        os.path.join("career_vault", "profile.json"),
    ]
    for target in json_targets:
        target_path = os.path.join(BASE_DIR, target)
        if os.path.exists(target_path):
            try:
                os.remove(target_path)
                deleted.append(target)
            except Exception as e:
                if verbose:
                    print(f"Warning: could not delete {target}: {e}")

    # 3. Chat sessions & exports
    chats_dir = os.path.join(BASE_DIR, "chats")
    if os.path.isdir(chats_dir):
        for f in os.listdir(chats_dir):
            if f.endswith(".json"):
                fp = os.path.join(chats_dir, f)
                try:
                    os.remove(fp)
                    deleted.append(os.path.relpath(fp, BASE_DIR))
                except Exception:
                    pass

    exports_dir = os.path.join(BASE_DIR, "career_vault", "exports")
    if os.path.isdir(exports_dir):
        for f in os.listdir(exports_dir):
            fp = os.path.join(exports_dir, f)
            try:
                if os.path.isfile(fp):
                    os.remove(fp)
                    deleted.append(os.path.relpath(fp, BASE_DIR))
            except Exception:
                pass

    # 4. Voiceprints and audio enrollments
    for pat in [os.path.join(BASE_DIR, "voiceprint*.npy"), os.path.join(BASE_DIR, "*.wav")]:
        for f in glob.glob(pat):
            try:
                os.remove(f)
                deleted.append(os.path.relpath(f, BASE_DIR))
            except Exception:
                pass

    for d in ["voice_enrollment", "voice_model"]:
        dp = os.path.join(BASE_DIR, d)
        if os.path.isdir(dp):
            try:
                shutil.rmtree(dp)
                deleted.append(d)
            except Exception:
                pass

    # 5. Logs & Temporary dumps
    for pat in [os.path.join(BASE_DIR, "*.log"), os.path.join(BASE_DIR, "logs", "*.log"), os.path.join(BASE_DIR, "logs", "*.txt")]:
        for f in glob.glob(pat):
            try:
                os.remove(f)
                deleted.append(os.path.relpath(f, BASE_DIR))
            except Exception:
                pass

    if verbose:
        print(f"Vega Data Reset Complete: {len(deleted)} files/directories wiped.")

    return {
        "status": "ok",
        "deleted_count": len(deleted),
        "deleted": deleted,
        "message": "All runtime memory, databases, and logs have been cleared."
    }


def main():
    parser = argparse.ArgumentParser(description="Reset Vega memory and databases for a clean fresh start.")
    parser.add_argument("-y", "--yes", action="store_true", help="Bypass confirmation prompt")
    args = parser.parse_args()

    res = reset_all_data(interactive=not args.yes, verbose=True)
    if res["status"] != "ok":
        sys.exit(1)


if __name__ == "__main__":
    main()
