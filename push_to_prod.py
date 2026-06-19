import subprocess
import sys

def run_cmd(cmd, capture_output=False):
    result = subprocess.run(cmd, text=True, capture_output=capture_output)
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


def ensure_clean_working_tree():
    status = git_output(["status", "--porcelain"])
    if status:
        print("ERROR: Working tree is not clean. Commit or stash changes before production deploy.")
        print("\nPending changes:")
        print(status)
        sys.exit(1)

print("\n===========================================")
print("WARNING: YOU ARE ABOUT TO PUSH TO PRODUCTION")
print("===========================================\n")

ensure_git_repo()
ensure_clean_working_tree()

run_cmd(["git", "fetch", "--prune", "origin"])

confirm = input("Type exactly 'deploy prod' to continue: ").strip()
if confirm != "deploy prod":
    print("\nDeployment cancelled. Safety first.")
    sys.exit(0)

print("\nInitiating production fast-forward merge...")

starting_branch = git_output(["branch", "--show-current"]) or "dev"

try:
    run_cmd(["git", "switch", "main"])
    run_cmd(["git", "pull", "--ff-only", "origin", "main"])

    ahead_count = int(git_output(["rev-list", "--count", "main..origin/dev"]) or "0")
    if ahead_count == 0:
        print("No new commits in origin/dev to deploy.")
        sys.exit(0)

    print("Merging origin/dev into main (ff-only)...")
    run_cmd(["git", "merge", "--ff-only", "origin/dev"])

    print("Pushing main to origin...")
    run_cmd(["git", "push", "origin", "main"])

    print("\nProduction deployment complete.")
finally:
    # Always restore the original branch for safer local workflow.
    current_branch = git_output(["branch", "--show-current"])
    if current_branch != starting_branch:
        run_cmd(["git", "switch", starting_branch])

print(f"Returned to branch: {starting_branch}")