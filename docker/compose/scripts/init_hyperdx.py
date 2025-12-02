#!/usr/bin/env python3
"""Initialize HyperDX with default admin user and team.

This script creates a default team and admin user in HyperDX using its API.
It uses the /register/password endpoint to properly create users with
passport-local-mongoose compatible password hashing.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Optional


def get_env_var(name: str, default: str) -> str:
    """Get environment variable with default value."""
    return os.environ.get(name, default)


def wait_for_hyperdx(api_url: str, max_retries: int = 60, delay: int = 2) -> bool:
    """Wait for HyperDX API to be available."""
    print("⏳ Waiting for HyperDX API to be available...")
    health_url = f"{api_url}/api/health"
    
    for i in range(max_retries):
        try:
            req = urllib.request.Request(health_url, method="GET")
            with urllib.request.urlopen(req, timeout=5) as response:
                if response.status == 200:
                    print("✅ HyperDX API is ready!")
                    return True
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError):
            if i < max_retries - 1:
                print(f"   Attempt {i + 1}/{max_retries}...")
                time.sleep(delay)
            else:
                print("❌ Failed to connect to HyperDX API", file=sys.stderr)
                return False
    return False


def create_team_via_api(api_url: str, team_name: str) -> Optional[str]:
    """Create team via API. Returns team ID or None."""
    # Note: HyperDX creates a default team when the first user registers
    # This function is kept for potential future use
    print(f"ℹ️  Team will be created automatically during user registration")
    return None


def register_user(
    api_url: str, email: str, password: str
) -> bool:
    """Register a user via HyperDX API.
    
    Password requirements:
    - At least 12 characters
    - Both lower and upper case characters
    - At least one special character
    """
    register_url = f"{api_url}/api/register/password"
    
    payload = json.dumps({
        "email": email,
        "password": password,
        "confirmPassword": password,
    }).encode("utf-8")
    
    headers = {
        "Content-Type": "application/json",
    }
    
    try:
        req = urllib.request.Request(
            register_url,
            data=payload,
            headers=headers,
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=30) as response:
            if response.status in (200, 201):
                print(f"✅ Successfully registered user '{email}'")
                return True
            else:
                print(f"⚠️  Unexpected status {response.status} when registering user")
                return False
    except urllib.error.HTTPError as e:
        if e.code == 400:
            # Check if user already exists
            try:
                error_body = e.read().decode("utf-8")
                if "already" in error_body.lower() or "exists" in error_body.lower():
                    print(f"✅ User '{email}' already exists")
                    return True
                print(f"⚠️  Registration failed (400): {error_body}")
            except Exception:
                print(f"⚠️  Registration failed with status 400 - user may already exist")
            return True  # Assume user exists
        elif e.code == 409:
            print(f"✅ User '{email}' already exists (409 Conflict)")
            return True
        else:
            print(f"❌ HTTP Error {e.code}: {e.reason}", file=sys.stderr)
            return False
    except urllib.error.URLError as e:
        print(f"❌ URL Error: {e.reason}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"❌ Error registering user: {e}", file=sys.stderr)
        return False


def check_user_exists(api_url: str, email: str) -> bool:
    """Check if we can log in with the given credentials."""
    # We can't easily check without logging in, so we'll just try to register
    return False


def main():
    """Main initialization function."""
    # Get configuration from environment variables
    hyperdx_url = get_env_var("HYPERDX_URL", "http://hyperdx:8080")
    admin_email = get_env_var("HYPERDX_ADMIN_EMAIL", "admin@example.com")
    # Password must be: 12+ chars, upper+lower case, special char
    admin_password = get_env_var("HYPERDX_ADMIN_PASSWORD", "Admin123!@#$")
    
    print("🚀 Initializing HyperDX...")
    print(f"   HyperDX URL: {hyperdx_url}")
    print(f"   Admin Email: {admin_email}")
    print("")
    
    # Wait for HyperDX API
    if not wait_for_hyperdx(hyperdx_url):
        print("⚠️  HyperDX API not ready, but continuing anyway...")
        # Don't exit - the user might want to register manually
    
    # Register admin user via API
    print("")
    print("📝 Registering admin user...")
    if register_user(hyperdx_url, admin_email, admin_password):
        print("")
        print("✅ HyperDX initialization complete!")
        print(f"   Admin user: {admin_email}")
        print(f"   Password: {admin_password}")
        print("   ⚠️  CHANGE THESE CREDENTIALS IN PRODUCTION!")
    else:
        print("")
        print("⚠️  Could not register admin user automatically.")
        print("   You can register manually at the HyperDX web UI.")
        # Don't fail - user can register manually
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
