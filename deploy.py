import sys
import subprocess

# Check if the user provided a commit message
if len(sys.argv) < 2:
    print('⚠️ Usage: python deploy.py "<commit_message>"')
    sys.exit(1)

commit_msg = sys.argv[1]

print("--------------------------------------------------")
print("📦 Packaging code on the DEV branch...")
subprocess.run(["git", "add", "."])
subprocess.run(["git", "commit", "-m", commit_msg])

# Note: We will uncomment these push commands once you link this to a new GitHub repo!
# subprocess.run(["git", "push", "origin", "dev"]) 

# print("\n☁️ PUSHED TO STAGING!")
# print("🌐 The Dev App is updating. Go test your changes now.")
# input("🛑 Press [ENTER] when you have verified the Staging app and are ready to deploy to Production...")

# print("\n🔀 Merging DEV into MAIN...")
# subprocess.run(["git", "checkout", "main"])
# subprocess.run(["git", "merge", "dev"])

# print("☁️ Pushing MAIN to Production...")
# subprocess.run(["git", "push", "origin", "main"]) 

# print("🔙 Returning back to DEV branch...")
# subprocess.run(["git", "checkout", "dev"])

print(f"🎉 Successfully committed locally! (Cloud push disabled until GitHub is linked).")