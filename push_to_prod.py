import subprocess
import sys

def run_cmd(cmd):
    result = subprocess.run(cmd, shell=True, text=True)
    if result.returncode != 0:
        print(f"\n❌ ERROR: Command failed -> {cmd}")
        sys.exit(1)

print("\n🚨 ========================================== 🚨")
print("🚨 WARNING: YOU ARE ABOUT TO PUSH TO PRODUCTION 🚨")
print("🚨 ========================================== 🚨\n")

confirm = input("Are you 100% sure the DEV app works perfectly? (type 'yes' to continue): ")
if confirm.lower() != 'yes':
    print("\n🛑 Deployment cancelled. Safety first! Go test in DEV.")
    sys.exit(0)

print("\n🚀 Initiating Production Merge...")

run_cmd("git checkout main")
run_cmd("git pull origin main")
print("\n🔀 Merging 'dev' into 'main'...")
run_cmd("git merge dev")

print("\n☁️  Pushing to GitHub (main branch)...")
run_cmd("git push origin main")

print("\n🔄 Switching back to 'dev' branch for safety...")
run_cmd("git checkout dev")

print("\n✅ LIVE DEPLOYMENT COMPLETE!")
print("💪 Your PROD app is now updated. You are safely back in your DEV sandbox.")