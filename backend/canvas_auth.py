"""
Canvas API Authentication
Handles Canvas LMS API authentication and token management

Built for: ReadySetClass v2.0
Based on: Canvas API OAuth documentation
"""

import requests
from typing import Dict, Optional
import os
import logging

logger = logging.getLogger(__name__)


class CanvasAuth:
    """
    Canvas Authentication Handler
    Supports manual access token authentication
    """

    def __init__(self, base_url: str, access_token: str):
        """
        Initialize Canvas authentication

        Args:
            base_url: Canvas instance URL (e.g., "https://vuu.instructure.com")
            access_token: Canvas API access token
        """
        self.base_url = base_url.rstrip('/')
        self.access_token = access_token
        self.headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }

    def test_connection(self) -> tuple[bool, Optional[Dict], Optional[str]]:
        """
        Test if the Canvas API token is valid

        Returns:
            tuple: (success: bool, user_data: dict or None, error_message: str or None)
        """
        try:
            # Enhanced debugging
            print(f"=== Canvas API Connection Test ===")
            print(f"URL: {self.base_url}/api/v1/users/self")
            print(f"Token length: {len(self.access_token)}")
            print(f"Token first 10 chars: {self.access_token[:10]}...")
            print(f"Token last 10 chars: ...{self.access_token[-10:]}")

            response = requests.get(
                f"{self.base_url}/api/v1/users/self",
                headers=self.headers,
                timeout=10
            )

            print(f"Canvas API response status: {response.status_code}")
            print(f"Response headers: {dict(response.headers)}")

            if response.status_code == 200:
                user_data = response.json()
                print(f"✅ Success! Authenticated as: {user_data.get('name')}")
                return True, user_data, None
            elif response.status_code == 401:
                error_details = response.text
                print(f"❌ 401 Unauthorized")
                print(f"Response body: {error_details}")

                # Check if it's a WWW-Authenticate issue
                www_auth = response.headers.get('WWW-Authenticate', '')
                if 'Bearer' in www_auth:
                    error_msg = "Invalid Canvas API token. The token format is correct, but Canvas rejected it. Please verify:\n1. Token was copied correctly\n2. Token hasn't expired\n3. Token has the required permissions"
                else:
                    error_msg = "Canvas authentication failed. Please check:\n1. Your Canvas URL is correct\n2. Your API token is valid\n3. Your token hasn't been revoked"

                return False, None, error_msg
            elif response.status_code == 403:
                error_msg = "Access forbidden. Your Canvas token doesn't have sufficient permissions. Please create a new token with full access."
                print(f"❌ 403 Forbidden: {response.text}")
                return False, None, error_msg
            elif response.status_code == 404:
                error_msg = f"Canvas API endpoint not found. Please verify your Canvas URL: {self.base_url}"
                print(f"❌ 404 Not Found: {response.text}")
                return False, None, error_msg
            else:
                error_msg = f"Canvas API error (HTTP {response.status_code}). Response: {response.text[:200]}"
                print(f"❌ Error {response.status_code}: {response.text}")
                return False, None, error_msg

        except requests.exceptions.SSLError as e:
            error_msg = f"SSL certificate error. Your Canvas URL may be incorrect: {str(e)}"
            print(f"❌ SSL Error: {e}")
            return False, None, error_msg
        except requests.exceptions.ConnectionError as e:
            error_msg = f"Cannot connect to Canvas at {self.base_url}. Please verify the URL is correct and Canvas is accessible."
            print(f"❌ Connection Error: {e}")
            return False, None, error_msg
        except requests.exceptions.Timeout as e:
            error_msg = "Connection to Canvas timed out. Please try again or check your network."
            print(f"❌ Timeout: {e}")
            return False, None, error_msg
        except requests.RequestException as e:
            error_msg = f"Connection test failed: {str(e)}"
            print(f"❌ Request Exception: {e}")
            return False, None, error_msg

    def get_user_profile(self) -> Optional[Dict]:
        """
        Get the authenticated user's Canvas profile

        Returns:
            dict: User profile data or None if failed
        """
        try:
            response = requests.get(
                f"{self.base_url}/api/v1/users/self/profile",
                headers=self.headers,
                timeout=10
            )

            if response.status_code == 200:
                return response.json()
            return None

        except requests.RequestException:
            return None


def _get_fernet():
    """Return a Fernet cipher using ENCRYPTION_KEY env var, or None if not configured."""
    encryption_key = os.getenv("ENCRYPTION_KEY")
    if not encryption_key:
        return None
    try:
        from cryptography.fernet import Fernet
        # Accept raw key or base64-encoded key
        key_bytes = encryption_key.encode() if isinstance(encryption_key, str) else encryption_key
        return Fernet(key_bytes)
    except Exception as e:
        logger.error(f"Failed to initialize Fernet cipher: {e}")
        return None


def encrypt_token(token: str) -> str:
    """
    Encrypt Canvas API token for secure storage using Fernet symmetric encryption.
    Falls back to plain text if ENCRYPTION_KEY is not set (logs a warning).
    """
    fernet = _get_fernet()
    if fernet is None:
        logger.warning("ENCRYPTION_KEY not set — storing Canvas token unencrypted. Set ENCRYPTION_KEY in environment variables.")
        return token
    return fernet.encrypt(token.encode()).decode()


def decrypt_token(encrypted_token: str) -> str:
    """
    Decrypt Canvas API token for use.
    Handles both Fernet-encrypted tokens and legacy plain-text tokens gracefully.
    """
    fernet = _get_fernet()
    if fernet is None:
        return encrypted_token
    try:
        from cryptography.fernet import InvalidToken
        return fernet.decrypt(encrypted_token.encode()).decode()
    except (InvalidToken, Exception):
        # Token was stored before encryption was enabled — return as-is
        logger.debug("Token appears to be unencrypted (legacy); returning as-is")
        return encrypted_token
