#!/usr/bin/env python3
"""Initialize HyperDX with MongoDB collections, default admin user, and ClickHouse datasource.

This script:
1. Creates required MongoDB collections for HyperDX
2. Creates a default admin user in HyperDX using its API
3. Verifies ClickHouse datasource is configured (via environment variables or API)
It uses the /register/password endpoint to properly create users with
passport-local-mongoose compatible password hashing.

Note: HyperDX should auto-create the ClickHouse datasource from DEFAULT_CONNECTIONS
and DEFAULT_SOURCES environment variables. This script verifies the setup is working.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Optional

try:
    from pymongo import MongoClient  # type: ignore
except ImportError:
    print("❌ pymongo is required. Install it with: pip install pymongo", file=sys.stderr)
    sys.exit(1)


def get_env_var(name: str, default: str) -> str:
    """Get environment variable with default value."""
    return os.environ.get(name, default)


def _retry_until_ready(probe: Callable[[], None], label: str, max_retries: int, delay: int) -> bool:
    """Retry probe() up to max_retries times, sleeping delay seconds between attempts."""
    print(f"⏳ Waiting for {label} to be available...")
    for i in range(max_retries):
        try:
            probe()
            print(f"✅ {label} is ready!")
            return True
        except Exception as e:
            if i < max_retries - 1:
                print(f"   Attempt {i + 1}/{max_retries}... ({e})")
                time.sleep(delay)
            else:
                print(f"❌ Failed to connect to {label}: {e}", file=sys.stderr)
    return False


def wait_for_mongodb(mongo_uri: str, max_retries: int = 60, delay: int = 2) -> bool:
    """Wait for MongoDB to be available."""
    def probe() -> None:
        with MongoClient(mongo_uri, serverSelectionTimeoutMS=5000) as client:
            client.admin.command('ping')
    return _retry_until_ready(probe, "MongoDB", max_retries, delay)


def create_mongodb_collections(mongo_uri: str, database_name: str = "hyperdx") -> bool:
    """Create required MongoDB collections for HyperDX."""
    print(f"📦 Creating MongoDB collections in database '{database_name}'...")

    collections = ['teams', 'users', 'dashboards', 'alerts', 'saved_searches']

    try:
        with MongoClient(mongo_uri, serverSelectionTimeoutMS=10000) as client:
            db = client[database_name]
            existing = set(db.list_collection_names())

            created_count = 0
            for collection_name in collections:
                if collection_name not in existing:
                    db.create_collection(collection_name)
                    print(f"   ✅ Created collection '{collection_name}'")
                    created_count += 1
                else:
                    print(f"   ℹ️  Collection '{collection_name}' already exists")

        skipped = len(collections) - created_count
        print(f"✅ MongoDB collections initialized ({created_count} new, {skipped} existing)")
        return True
    except Exception as e:
        print(f"❌ Error creating MongoDB collections: {e}", file=sys.stderr)
        return False


def wait_for_hyperdx(api_url: str, max_retries: int = 60, delay: int = 2) -> bool:
    """Wait for HyperDX API to be available."""
    def probe() -> None:
        req = urllib.request.Request(f"{api_url}/api/health", method="GET")
        with urllib.request.urlopen(req, timeout=5) as response:
            if response.status != 200:
                raise RuntimeError(f"status {response.status}")
    return _retry_until_ready(probe, "HyperDX API", max_retries, delay)


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
                print("⚠️  Registration failed with status 400 - user may already exist")
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


def login_user(api_url: str, email: str, password: str) -> Optional[str]:
    """Login to HyperDX and return session token/cookie.

    Returns the session token if successful, None otherwise.
    """
    login_url = f"{api_url}/api/login/password"

    payload = json.dumps({
        "email": email,
        "password": password,
    }).encode("utf-8")

    headers = {
        "Content-Type": "application/json",
    }

    try:
        req = urllib.request.Request(
            login_url,
            data=payload,
            headers=headers,
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=30) as response:
            if response.status == 200:
                cookies: Optional[str] = response.headers.get("Set-Cookie")
                if cookies:
                    print(f"✅ Successfully logged in as '{email}'")
                    cookie_pair = cookies.split(";", 1)[0].strip()
                    return f"cookie:{cookie_pair}"
                try:
                    body = json.loads(response.read().decode("utf-8"))
                    if isinstance(body, dict):
                        token = body.get("token") or body.get("session")
                        if token:
                            return f"bearer:{token}"
                except Exception as parse_err:
                    print(f"   ⚠️  Could not parse login response body: {parse_err}")
                print(f"✅ Successfully logged in as '{email}' (no session token extracted)")
                return None
            else:
                print(f"⚠️  Unexpected status {response.status} when logging in")
                return None
    except urllib.error.HTTPError as e:
        if e.code == 401:
            print(f"❌ Invalid credentials for '{email}'", file=sys.stderr)
        else:
            print(f"❌ HTTP Error {e.code}: {e.reason}", file=sys.stderr)
        return None
    except urllib.error.URLError as e:
        print(f"❌ URL Error: {e.reason}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"❌ Error logging in: {e}", file=sys.stderr)
        return None


def verify_clickhouse_connection(api_url: str, session_token: Optional[str] = None) -> None:
    """Log ClickHouse datasource status. Best-effort — auto-configured via env vars."""
    print("🔍 Verifying ClickHouse datasource configuration...")

    connections_url = f"{api_url}/api/connections"

    headers: dict[str, str] = {
        "Content-Type": "application/json",
    }

    if session_token:
        if session_token.startswith("cookie:"):
            headers["Cookie"] = session_token[len("cookie:"):]
        elif session_token.startswith("bearer:"):
            headers["Authorization"] = f"Bearer {session_token[len('bearer:'):]}"
        else:
            headers["Authorization"] = f"Bearer {session_token}"

    try:
        req = urllib.request.Request(
            connections_url,
            headers=headers,
            method="GET"
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            if response.status == 200:
                data = json.loads(response.read().decode("utf-8"))
                connections = data.get("connections", []) if isinstance(data, dict) else []
                if connections:
                    print(f"   ✅ Found {len(connections)} ClickHouse connection(s)")
                    for conn in connections:
                        name = conn.get("name", "Unknown")
                        print(f"      - {name}")
                else:
                    print("   ⚠️  No connections found (may be using environment variables)")
            else:
                print(f"   ⚠️  Could not verify connections (status {response.status})")
    except urllib.error.HTTPError as e:
        if e.code == 401:
            print("   ⚠️  Authentication required to verify connections")
        else:
            print(f"   ⚠️  Could not verify connections (HTTP {e.code})")
        print("   ℹ️  Datasource should be auto-configured via environment variables")
    except Exception as e:
        print(f"   ⚠️  Could not verify connections: {e}")
        print("   ℹ️  Datasource should be auto-configured via environment variables")


def main() -> int:
    """Main initialization function."""
    hyperdx_url = get_env_var("HYPERDX_URL", "http://hyperdx:8080")
    mongo_uri = get_env_var("MONGO_URI", "mongodb://mongodb:27017/hyperdx")
    mongo_db = get_env_var("MONGO_DB", "hyperdx")
    admin_email = get_env_var("HYPERDX_ADMIN_EMAIL", "admin@example.com")
    admin_password = get_env_var("HYPERDX_ADMIN_PASSWORD", "Admin123!@#$")

    print("🚀 Initializing HyperDX...")
    print(f"   HyperDX URL: {hyperdx_url}")
    print(f"   MongoDB URI: {mongo_uri}")
    print(f"   MongoDB Database: {mongo_db}")
    print(f"   Admin Email: {admin_email}")
    print("")

    print("=" * 60)
    print("Step 1: MongoDB Initialization")
    print("=" * 60)
    if not wait_for_mongodb(mongo_uri):
        print("⚠️  MongoDB not ready, but continuing anyway...")
        print("   Collections may be auto-created by HyperDX")
    else:
        create_mongodb_collections(mongo_uri, mongo_db)

    print("")
    print("=" * 60)
    print("Step 2: HyperDX User Registration")
    print("=" * 60)
    if not wait_for_hyperdx(hyperdx_url):
        print("⚠️  HyperDX API not ready, but continuing anyway...")
        print("   You may need to register manually at the HyperDX web UI.")
        return 1

    print("")
    print("📝 Registering admin user...")
    if not register_user(hyperdx_url, admin_email, admin_password):
        print("")
        print("⚠️  Could not register admin user automatically.")
        print("   You can register manually at the HyperDX web UI.")
        return 1

    print("")
    print("=" * 60)
    print("Step 3: ClickHouse Datasource Verification")
    print("=" * 60)
    print("")
    print("🔐 Logging in to verify setup...")
    session_token = login_user(hyperdx_url, admin_email, admin_password)

    if not session_token:
        print("   ⚠️  Could not login to verify datasource")
        print("   ℹ️  Datasource should be auto-configured via DEFAULT_CONNECTIONS env var")

    verify_clickhouse_connection(hyperdx_url, session_token)

    print("")
    print("=" * 60)
    print("✅ HyperDX initialization complete!")
    print("=" * 60)
    print(f"   Admin user: {admin_email}")
    print(f"   Password: {admin_password}")
    print("   ⚠️  CHANGE THESE CREDENTIALS IN PRODUCTION!")
    print("")
    print("   ClickHouse datasource should be auto-configured via environment variables.")
    print("   If you don't see data in HyperDX, check:")
    print("   1. OTEL Collector is running and connected to ClickHouse")
    print("   2. ClickHouse tables are created (init-clickhouse service)")
    print("   3. HyperDX DEFAULT_CONNECTIONS and DEFAULT_SOURCES are set correctly")
    print("")

    return 0


if __name__ == "__main__":
    sys.exit(main())
