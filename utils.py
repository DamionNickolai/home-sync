"""Utility functions for home-sync app."""

import re

def calculate_next_version(current_version, categories_in_release):
    """
    Calculates the next semantic version based on the release types.
    
    Versioning rules:
    - Core changes → bump MAJOR version
    - UI changes → bump MINOR version  
    - Bug fixes → bump PATCH version
    - Higher priority changes reset lower priorities to 0
    
    Examples:
    - 1.0.0 + Core → 2.0.0
    - 1.0.0 + UI → 1.1.0
    - 1.0.0 + Bug → 1.0.1
    - 1.2.3 + Core → 2.0.0 (resets minor and patch)
    """
    try:
        # Supports values like "v1.2.3" and "1.2.3-alpha".
        match = re.search(r"(\d+)\.(\d+)\.(\d+)", str(current_version))
        if not match:
            return current_version

        major, minor, patch = map(int, match.groups())
        if "Core" in categories_in_release:
            major += 1
            minor = 0
            patch = 0
        elif "UI" in categories_in_release:
            minor += 1
            patch = 0
        elif "Bug" in categories_in_release:
            patch += 1
        return f"{major}.{minor}.{patch}"
    except Exception:
        return current_version
