# Google Drive OAuth2 authentication and file management

import io
import os
import logging
from pathlib import Path
from typing import Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

logger = logging.getLogger(__name__)

# OAuth2 scopes — read-only Drive access
SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

# File paths
CREDENTIALS_FILE = "credentials.json"
TOKEN_FILE       = "token.json"
TEMP_DIR         = Path("tmp")

# Supported MIME types ↔ extensions
SUPPORTED_MIME = {
    "application/pdf":                                                     ".pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "text/plain":                                                          ".txt",
    # Google Docs → export as docx
    "application/vnd.google-apps.document":                               ".docx",
}

GOOGLE_DOCS_EXPORT_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def authenticate() -> object:
    """
    Runs OAuth2 flow (first time opens a browser), then reuses saved token.
    Returns an authenticated Google Drive service object.
    """
    if not Path(CREDENTIALS_FILE).exists():
        raise FileNotFoundError(
            f"'{CREDENTIALS_FILE}' not found. Download it from Google Cloud Console "
            "(APIs & Services → Credentials → OAuth 2.0 Client IDs → Desktop App → Download JSON)."
        )

    creds: Optional[Credentials] = None

    if Path(TOKEN_FILE).exists():
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            logger.info("Refreshing expired credentials...")
            creds.refresh(Request())
        else:
            logger.info("Starting OAuth2 flow (browser will open)...")
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=8080)

        with open(TOKEN_FILE, "w") as token:
            token.write(creds.to_json())
        logger.info(f"Token saved to {TOKEN_FILE}")

    service = build("drive", "v3", credentials=creds)
    logger.info("Google Drive authenticated successfully.")
    return service


def list_files(service, folder_id: str) -> list[dict]:
    """
    Lists all supported files inside a Google Drive folder.
    Returns list of dicts: {id, name, mimeType, webViewLink}
    """
    mime_filter = " or ".join(
        [f"mimeType='{m}'" for m in SUPPORTED_MIME.keys()]
    )
    query = f"'{folder_id}' in parents and ({mime_filter}) and trashed=false"

    results = (
        service.files()
        .list(
            q=query,
            fields="files(id, name, mimeType, webViewLink)",
            pageSize=50,
        )
        .execute()
    )

    files = results.get("files", [])
    logger.info(f"Found {len(files)} supported file(s) in folder '{folder_id}'.")
    return files


def download_file(service, file_id: str, file_name: str, mime_type: str) -> Path:
    """
    Downloads a Drive file to the tmp/ directory.
    Google Docs are exported to .docx format automatically.
    Returns the local file path.
    """
    TEMP_DIR.mkdir(exist_ok=True)
    ext = SUPPORTED_MIME.get(mime_type, ".bin")
    safe_name = "".join(c if c.isalnum() or c in "._- " else "_" for c in file_name)
    local_path = TEMP_DIR / f"{safe_name}{'' if safe_name.endswith(ext) else ext}"

    fh = io.BytesIO()

    if mime_type == "application/vnd.google-apps.document":
        # Export Google Doc as DOCX
        request = service.files().export_media(
            fileId=file_id, mimeType=GOOGLE_DOCS_EXPORT_MIME
        )
    else:
        request = service.files().get_media(fileId=file_id)

    downloader = MediaIoBaseDownload(fh, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()

    local_path.write_bytes(fh.getvalue())
    logger.info(f"Downloaded '{file_name}' → {local_path}")
    return local_path
