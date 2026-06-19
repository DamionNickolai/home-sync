import re
import subprocess
import sys


def run_cmd(cmd, capture_output=False):
    result = subprocess.run(
        cmd,
        text=True,
        capture_output=capture_output,
    )
    if result.returncode != 0:
        print(f"\nERROR: Command failed -> {' '.join(cmd)}")
        if result.stdout:
            print(result.stdout.strip())
        if result.stderr:
            print(result.stderr.strip())
        sys.exit(1)
    return result


def git_output(args):
    return run_cmd(["git", *args], capture_output=True).stdout.strip()


def ensure_git_repo():
    inside = git_output(["rev-parse", "--is-inside-work-tree"])
    if inside.lower() != "true":
        print("ERROR: This script must be run inside a git repository.")
        sys.exit(1)


def working_tree_has_changes():
    return bool(git_output(["status", "--porcelain"]))


def validate_version(version):
    # Accepts semantic versions like 1.2.3 or 1.2.3-alpha
    return bool(re.match(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$", version))


def update_app_version(new_version):
    try:
        with open("home_sync.py", "r", encoding="utf-8") as f:
            content = f.read()

        updated_content, replacements = re.subn(
            r'^(APP_VERSION\s*=\s*")[^"]*(")',
            rf'\1{new_version}\2',
            content,
            count=1,
            flags=re.MULTILINE,
        )

        if replacements != 1:
            print("ERROR: Could not find APP_VERSION assignment in home_sync.py")
            sys.exit(1)

        with open("home_sync.py", "w", encoding="utf-8") as f:
            f.write(updated_content)

        print(f"Updated home_sync.py APP_VERSION to {new_version}")
    except OSError as exc:
        print(f"ERROR: Failed to update version: {exc}")
        sys.exit(1)


def main():
    print("\nPreparing to push to DEV (staging)...")
    ensure_git_repo()

    print("Fetching latest remote refs...")
    run_cmd(["git", "fetch", "origin", "dev"])

    print("Switching to dev branch...")
    run_cmd(["git", "switch", "dev"])

    print("Syncing local dev with origin/dev (fast-forward only)...")
    run_cmd(["git", "pull", "--ff-only", "origin", "dev"])

    bump = input("Bump APP_VERSION in home_sync.py? (y/n): ").strip().lower()
    if bump == "y":
        new_version = input("Enter new version (e.g., 1.4.0): ").strip()
        if not validate_version(new_version):
            print("ERROR: Invalid version format. Use semantic versioning, e.g., 1.4.0")
            sys.exit(1)
        update_app_version(new_version)

    if not working_tree_has_changes():
        print("No local changes detected. Nothing to commit.")
        push_only = input("Push dev branch anyway? (y/n): ").strip().lower()
        if push_only == "y":
            run_cmd(["git", "push", "origin", "dev"])
            print("Dev branch push complete.")
        else:
            print("No deployment action taken.")
        return

    print("\nCurrent changes:")
    run_cmd(["git", "status", "--short"])

    confirm_stage = input("Stage all current changes and continue? (y/n): ").strip().lower()
    if confirm_stage != "y":
        print("Cancelled. No changes were committed.")
        return

    run_cmd(["git", "add", "-A"])

    # If nothing staged (rare edge case), abort safely.
    staged_status = git_output(["diff", "--cached", "--name-only"])
    if not staged_status:
        print("No staged changes found after git add -A. Aborting.")
        return

    commit_msg = input("Commit message (Enter for default): ").strip() or "Routine DEV update"
    run_cmd(["git", "commit", "-m", commit_msg])

    print("Pushing to origin/dev...")
    run_cmd(["git", "push", "origin", "dev"])

    print("\nDev deployment push complete.")
    print("Next: validate in staging, then run python push_to_prod.py when ready.")


if __name__ == "__main__":
    main()