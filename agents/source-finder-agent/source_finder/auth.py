# fetches a Google Cloud access token for authenticating Vertex AI Search requests

import google.auth
import google.auth.transport.requests


def get_access_token() -> str:
    """Get a short-lived Google Cloud access token using the default service account."""
    credentials, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    auth_req = google.auth.transport.requests.Request()
    credentials.refresh(auth_req)
    return credentials.token
