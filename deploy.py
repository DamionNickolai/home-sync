import sys
import subprocess
import re

# Check if the user provided a commit message
if len(sys.argv) < 2:
    print('⚠️ Usage: python deploy.py "<commit_message>"')
    sys.exit(1)

commit_msg = sys.argv[1]

# 🟢 THE AUTO-VERSIONING PROMPT
print("--------------------------------------------------")
bump = input("🚀 Do you want to bump the app version for this release? (y/n): ").strip().lower()

if bump == 'y':
    new_version = input("📝 Enter the new version (e.g., 1.4.0): ").strip()
    
    try:
        # 1. Open the main app file to read it
        with open("home_sync.py", "r", encoding="utf-8") as f:
            content = f.read()
            
        # 2. Find the APP_VERSION line and swap in the new number
        content = re.sub(r'APP_VERSION\s*=\s*".*"', f'APP_VERSION = "{new_version}"', content)
        
        # 3. Save the file
        with open("home_sync.py", "w", encoding="utf-8") as f:
            f.write(content)
            
        print(f"✅ Success! home_sync.py updated to v{new_version}")
        
        # 🟢 MOVED THE SAFETY CHECK INSIDE THE BUMP LOGIC
        print("--------------------------------------------------")
        input("🛑 SAFETY CHECK: Confirm Submitted Version. Press [ENTER] to push to DEV, or press [Ctrl + C] to cancel deployment...")
        
    except Exception as e:
        print(f"❌ Failed to update version: {e}")
        sys.exit(1) # Stop the deployment if the file write fails

print("--------------------------------------------------")
print("📦 Packaging code on the DEV branch...")
subprocess.run(["git", "add", "."])
subprocess.run(["git", "commit", "-m", commit_msg])
subprocess.run(["git", "push", "origin", "dev"]) 

print("\n☁️ PUSHED TO STAGING!")
print("🌐 The Dev App is updating. Go test your changes now.")

# The script will completely freeze here until you press Enter
input("🛑 Press [ENTER] when you have verified the Staging app and are ready to deploy to Production...")

print("\n🔀 Merging DEV into MAIN...")
subprocess.run(["git", "checkout", "main"])
subprocess.run(["git", "merge", "dev"])

print("☁️ Pushing MAIN to Production...")
subprocess.run(["git", "push", "origin", "main"]) 

print("🔙 Returning back to DEV branch...")
subprocess.run(["git", "checkout", "dev"])

print(f"🎉 Successfully deployed! Your workspace is ready for the next feature.")