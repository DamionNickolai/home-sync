import sys
import subprocess
import re

def run_cmd(cmd):
    """Runs a terminal command and stops the script if it fails."""
    result = subprocess.run(cmd, text=True)
    if result.returncode != 0:
        print(f"\n❌ ERROR: Command failed -> {' '.join(cmd)}")
        sys.exit(1)

print("\n🚀 Preparing to push to DEV (Staging Environment)...")

# 1. THE AUTO-VERSIONING PROMPT (Your code!)
print("--------------------------------------------------")
bump = input("🚀 Do you want to bump the app version for this release? (y/n): ").strip().lower()

if bump == 'y':
    new_version = input("📝 Enter the new version (e.g., 1.4.0): ").strip()
    
    try:
        with open("home_sync.py", "r", encoding="utf-8") as f:
            content = f.read()
            
        content = re.sub(r'APP_VERSION\s*=\s*".*"', f'APP_VERSION = "{new_version}"', content)
        
        with open("home_sync.py", "w", encoding="utf-8") as f:
            f.write(content)
            
        print(f"✅ Success! home_sync.py updated to v{new_version}")
    except Exception as e:
        print(f"❌ Failed to update version: {e}")
        sys.exit(1)

# 2. COMMIT MESSAGE
print("--------------------------------------------------")
commit_msg = input("📝 Enter a commit message (or press Enter for default): ").strip()
if not commit_msg:
    commit_msg = "Routine DEV update"

# 3. GIT OPERATIONS
print("\n🔄 Switching to 'dev' branch...")
run_cmd(["git", "checkout", "dev"])

print("📦 Packaging code...")
run_cmd(["git", "add", "."])
run_cmd(["git", "commit", "-m", commit_msg])

print("☁️ Pushing to GitHub (dev branch)...")
run_cmd(["git", "push", "origin", "dev"])

print("\n✅ PUSHED TO STAGING!")
print("📱 Go test the HOME-SYNC-DEV app on your phone.")
print("💡 Whenever you are ready to update the live app, run: python push_to_prod.py")