"""
YouTube Upload Module
"""

import os
from pathlib import Path
from typing import Optional, Dict
from loguru import logger


class YouTubeUploader:
    """Handle YouTube video uploads"""
    
    def __init__(self):
        self.logger = logger
        self.youtube_service = None
    
    def _get_youtube_service(self):
        """Initialize YouTube API service"""
        from src.core import config
        import pickle
        
        try:
            from google.auth.transport.requests import Request
            from google.oauth2.service_account import Credentials
            from google_auth_oauthlib.flow import InstalledAppFlow
            from googleapiclient.discovery import build
            
            CLIENT_SECRETS_FILE = "youtube_oauth.json"
            SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
            
            credentials = None
            
            # Load credentials from file if exists
            if os.path.exists("youtube_token.pickle"):
                with open("youtube_token.pickle", "rb") as token:
                    credentials = pickle.load(token)
            
            # If not, create new credentials
            if not credentials or not credentials.valid:
                if credentials and credentials.expired and credentials.refresh_token:
                    credentials.refresh(Request())
                else:
                    flow = InstalledAppFlow.from_client_secrets_file(
                        CLIENT_SECRETS_FILE, SCOPES
                    )
                    credentials = flow.run_local_server(port=0)
                
                # Save credentials for future use
                with open("youtube_token.pickle", "wb") as token:
                    pickle.dump(credentials, token)
            
            self.youtube_service = build("youtube", "v3", credentials=credentials)
            return self.youtube_service
            
        except Exception as e:
            self.logger.error(f"Error initializing YouTube service: {e}")
            raise
    
    def upload_video(
        self,
        video_path: str,
        title: str,
        description: str,
        tags: Optional[list[str]] = None,
        category_id: str = "22",  # People & Blogs
        is_public: bool = True,
        thumbnail_path: Optional[str] = None
    ) -> str:
        """Upload video to YouTube"""
        try:
            youtube = self._get_youtube_service()
            from googleapiclient.http import MediaFileUpload
            
            body = {
                "snippet": {
                    "title": title,
                    "description": description,
                    "tags": tags or [],
                    "categoryId": category_id
                },
                "status": {
                    "privacyStatus": "public" if is_public else "private",
                    "selfDeclaredMadeForKids": False
                }
            }
            
            # Upload video
            self.logger.info(f"Uploading video: {title}")
            
            request = youtube.videos().insert(
                part="snippet,status",
                body=body,
                media_body=MediaFileUpload(video_path, chunksize=-1, resumable=True),
                notifySubscribers=False
            )
            
            response = request.execute()
            video_id = response["id"]
            
            # Upload thumbnail if provided
            if thumbnail_path:
                self._upload_thumbnail(youtube, video_id, thumbnail_path)
            
            self.logger.info(f"Video uploaded successfully! Video ID: {video_id}")
            return video_id
            
        except Exception as e:
            self.logger.error(f"Error uploading video: {e}")
            raise
    
    def _upload_thumbnail(
        self,
        youtube_service,
        video_id: str,
        thumbnail_path: str
    ):
        """Upload thumbnail for video"""
        try:
            from googleapiclient.http import MediaFileUpload

            youtube_service.thumbnails().set(
                videoId=video_id,
                media_body=MediaFileUpload(thumbnail_path)
            ).execute()
            
            self.logger.info(f"Thumbnail uploaded for video {video_id}")
        except Exception as e:
            self.logger.warning(f"Error uploading thumbnail: {e}")
    
    def schedule_video(
        self,
        video_path: str,
        title: str,
        description: str,
        publish_time: str,  # ISO 8601 format: "2024-01-15T15:00:00Z"
        tags: Optional[list[str]] = None,
        category_id: str = "22"
    ) -> str:
        """Schedule video for later publishing"""
        try:
            youtube = self._get_youtube_service()
            from googleapiclient.http import MediaFileUpload
            
            body = {
                "snippet": {
                    "title": title,
                    "description": description,
                    "tags": tags or [],
                    "categoryId": category_id
                },
                "status": {
                    "privacyStatus": "private",
                    "publishAt": publish_time,
                    "selfDeclaredMadeForKids": False
                }
            }
            
            self.logger.info(f"Scheduling video: {title} for {publish_time}")
            
            request = youtube.videos().insert(
                part="snippet,status",
                body=body,
                media_body=MediaFileUpload(video_path, chunksize=-1, resumable=True),
                notifySubscribers=False
            )
            
            response = request.execute()
            video_id = response["id"]
            
            self.logger.info(f"Video scheduled! Video ID: {video_id}")
            return video_id
            
        except Exception as e:
            self.logger.error(f"Error scheduling video: {e}")
            raise
    
    def add_to_playlist(
        self,
        video_id: str,
        playlist_id: str
    ):
        """Add video to playlist"""
        try:
            youtube = self._get_youtube_service()
            
            youtube.playlistItems().insert(
                part="snippet",
                body={
                    "snippet": {
                        "playlistId": playlist_id,
                        "resourceId": {
                            "kind": "youtube#video",
                            "videoId": video_id
                        }
                    }
                }
            ).execute()
            
            self.logger.info(f"Added video {video_id} to playlist {playlist_id}")
        except Exception as e:
            self.logger.error(f"Error adding to playlist: {e}")
            raise
