"""Google Drive + Sheets — optional publisher.

Only active when USE_DRIVE=true in .env.
Requires google_credentials.json (Service Account) in the video_factory directory.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from loguru import logger

from config import settings


def upload_to_drive(file_path: Path, filename: str) -> Optional[str]:
    """Upload file to Google Drive. Returns webViewLink or None."""
    if not settings.use_drive:
        return None

    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload

        creds_file = Path(__file__).resolve().parent.parent / "google_credentials.json"
        if not creds_file.exists():
            logger.warning("google_credentials.json not found")
            return None

        creds = service_account.Credentials.from_service_account_file(
            str(creds_file),
            scopes=["https://www.googleapis.com/auth/drive.file"],
        )
        service = build("drive", "v3", credentials=creds)

        file_metadata = {
            "name": filename,
            "parents": [settings.google_drive_folder_id],
        }
        media = MediaFileUpload(str(file_path), resumable=True)
        uploaded = service.files().create(
            body=file_metadata, media_body=media, fields="id,webViewLink"
        ).execute()

        link = uploaded.get("webViewLink", f"https://drive.google.com/file/d/{uploaded['id']}")
        logger.info(f"Uploaded to Drive: {filename}")
        return link

    except ImportError:
        logger.warning("Google Drive SDK not installed. Run: pip install google-api-python-client google-auth")
        return None
    except Exception as e:
        logger.error(f"Drive upload error: {e}")
        return None


def log_to_sheets(data: dict) -> bool:
    """Append a row to Google Sheets. Returns success."""
    if not settings.use_drive or not settings.google_sheets_id:
        return False

    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        creds_file = Path(__file__).resolve().parent.parent / "google_credentials.json"
        if not creds_file.exists():
            return False

        creds = service_account.Credentials.from_service_account_file(
            str(creds_file),
            scopes=["https://www.googleapis.com/auth/spreadsheets"],
        )
        service = build("sheets", "v4", credentials=creds)

        values = [[
            data.get("fecha", ""),
            data.get("nicho", ""),
            data.get("titulo", ""),
            data.get("gancho", ""),
            data.get("cta", ""),
            data.get("caption", ""),
            data.get("hook_score", ""),
            data.get("score_desarrollo", ""),
            data.get("score_cierre", ""),
            data.get("quality_score", ""),
            data.get("quality_status", ""),
            data.get("ab_variant", ""),
            data.get("viral_score", ""),
            data.get("velocidad", ""),
            data.get("tts_engine", ""),
            data.get("plataforma", ""),
            data.get("num_clips", ""),
            data.get("drive_link", ""),
            data.get("timestamp", ""),
        ]]

        service.spreadsheets().values().append(
            spreadsheetId=settings.google_sheets_id,
            range="Videos!A:S",
            valueInputOption="RAW",
            body={"values": values},
        ).execute()

        logger.info("Logged to Google Sheets")
        return True

    except ImportError:
        return False
    except Exception as e:
        logger.error(f"Sheets log error: {e}")
        return False
