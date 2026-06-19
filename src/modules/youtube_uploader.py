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
            SCOPES = [
                "https://www.googleapis.com/auth/youtube.upload",
                "https://www.googleapis.com/auth/youtube.readonly",
            ]
            
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

    def list_channel_videos(self, max_results: int = 200) -> list[dict]:
        """Fetch all published video titles/IDs from the authenticated channel.

        Returns list of {"id": str, "title": str, "published_at": str}.
        Used to avoid suggesting topics already covered.
        """
        try:
            youtube = self._get_youtube_service()

            # Get the channel's uploads playlist
            channels_resp = youtube.channels().list(
                part="contentDetails", mine=True
            ).execute()
            items = channels_resp.get("items", [])
            if not items:
                self.logger.warning("No channel found for authenticated user")
                return []

            uploads_playlist = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]

            # Paginate through all uploads
            videos: list[dict] = []
            next_page = None
            while len(videos) < max_results:
                page_size = min(50, max_results - len(videos))
                request = youtube.playlistItems().list(
                    part="snippet",
                    playlistId=uploads_playlist,
                    maxResults=page_size,
                    pageToken=next_page,
                )
                response = request.execute()

                for item in response.get("items", []):
                    snippet = item.get("snippet", {})
                    videos.append({
                        "id": snippet.get("resourceId", {}).get("videoId", ""),
                        "title": snippet.get("title", ""),
                        "published_at": snippet.get("publishedAt", ""),
                    })

                next_page = response.get("nextPageToken")
                if not next_page:
                    break

            self.logger.info(f"Fetched {len(videos)} published videos from channel")
            return videos

        except Exception as e:
            self.logger.error(f"Error listing channel videos: {e}")
            return []

    def get_channel_titles(self, max_results: int = 200) -> list[str]:
        """Return just the titles of all published videos on the channel."""
        return [v["title"] for v in self.list_channel_videos(max_results) if v.get("title")]

    def analyze_competitors(self, query: str, max_results: int = 25) -> dict:
        """Fetch top-ranking YouTube videos for a query and extract SEO insights.

        Inspired by the Advanced YouTube SEO Generator approach: use the YouTube
        Data API to find what titles, tags, and descriptions actually rank for
        a topic, then feed that data into our LLM-based SEO generation.

        Returns:
            {
                "query": str,
                "top_titles": [str],        # titles of top-ranking videos
                "common_tags": [str],        # most frequent tags across top videos
                "common_title_words": [str], # most frequent meaningful words in titles
                "avg_views": int,
                "top_hashtags": [str],
                "descriptions_sample": [str], # first 200 chars of top 5 descriptions
            }
        """
        try:
            youtube = self._get_youtube_service()

            # Search for top videos on this topic
            search_resp = youtube.search().list(
                part="snippet",
                q=query,
                type="video",
                order="relevance",
                maxResults=max_results,
            ).execute()

            video_ids = [
                item["id"]["videoId"]
                for item in search_resp.get("items", [])
                if item.get("id", {}).get("videoId")
            ]

            if not video_ids:
                self.logger.warning(f"No competitor videos found for: {query}")
                return {"query": query, "top_titles": [], "common_tags": [], "common_title_words": [], "avg_views": 0, "top_hashtags": [], "descriptions_sample": []}

            # Fetch full details (title, tags, description, view count)
            details_resp = youtube.videos().list(
                part="snippet,statistics",
                id=",".join(video_ids[:50]),
            ).execute()

            titles: list[str] = []
            all_tags: list[str] = []
            all_title_words: list[str] = []
            view_counts: list[int] = []
            descriptions: list[str] = []
            hashtags: list[str] = []

            import re
            stop_words = {
                "the", "a", "an", "is", "are", "was", "were", "of", "in", "to",
                "and", "for", "on", "with", "that", "this", "it", "you", "your",
                "how", "what", "why", "who", "when", "which", "from", "by", "not",
                "but", "or", "do", "does", "did", "will", "can", "has", "have",
                "had", "be", "been", "about", "more", "most", "very", "just",
                "than", "then", "also", "so", "if", "my", "me", "we", "our",
                "all", "no", "one", "two", "get", "make", "top", "best", "new",
            }

            for item in details_resp.get("items", []):
                snippet = item.get("snippet", {})
                stats = item.get("statistics", {})

                title = snippet.get("title", "")
                titles.append(title)

                # Extract title words (meaningful, >2 chars)
                words = re.findall(r"[a-zA-ZÀ-ỹ]{3,}", title.lower())
                all_title_words.extend(w for w in words if w not in stop_words)

                # Collect tags
                tags = snippet.get("tags", [])
                all_tags.extend(tags)

                # View count
                views = int(stats.get("viewCount", 0))
                view_counts.append(views)

                # Description (first 200 chars for analysis)
                desc = snippet.get("description", "")
                if desc:
                    descriptions.append(desc[:200])

                # Extract hashtags from description
                desc_hashtags = re.findall(r"#\w+", desc)
                hashtags.extend(desc_hashtags)

            # Count frequency and rank
            from collections import Counter
            tag_counts = Counter(all_tags)
            word_counts = Counter(all_title_words)
            hashtag_counts = Counter(hashtags)

            result = {
                "query": query,
                "top_titles": titles[:10],
                "common_tags": [tag for tag, _ in tag_counts.most_common(20)],
                "common_title_words": [w for w, _ in word_counts.most_common(15)],
                "avg_views": int(sum(view_counts) / max(len(view_counts), 1)),
                "top_hashtags": [h for h, _ in hashtag_counts.most_common(10)],
                "descriptions_sample": descriptions[:5],
            }

            self.logger.info(
                f"Competitor analysis for '{query}': {len(titles)} videos, "
                f"{len(tag_counts)} unique tags, avg {result['avg_views']:,} views"
            )
            return result

        except Exception as e:
            self.logger.error(f"Error analyzing competitors: {e}")
            return {"query": query, "top_titles": [], "common_tags": [], "common_title_words": [], "avg_views": 0, "top_hashtags": [], "descriptions_sample": []}

    @staticmethod
    def calculate_seo_score(title: str, description: str, tags: list[str]) -> dict:
        """Calculate an SEO quality score for metadata before uploading.

        Returns dict with total score (0-100) and per-field feedback.
        """
        feedback: list[str] = []
        score = 0

        # Title checks (0-30 points)
        if title:
            if len(title) <= 70:
                score += 10
            else:
                feedback.append(f"Title too long ({len(title)} chars, max 70)")
            if len(title) >= 30:
                score += 5
            else:
                feedback.append("Title too short (aim for 30-70 chars)")
            # Check for number/curiosity element
            import re
            if re.search(r"\d", title):
                score += 8
            else:
                feedback.append("Title has no numbers — numbers boost CTR")
            if any(c in title for c in "?!"):
                score += 7
            else:
                feedback.append("Consider ending title with ? or ! for curiosity")
        else:
            feedback.append("Missing title")

        # Description checks (0-30 points)
        if description:
            desc_len = len(description)
            if desc_len >= 300:
                score += 15
            elif desc_len >= 100:
                score += 8
                feedback.append(f"Description short ({desc_len} chars, aim for 300+)")
            else:
                feedback.append(f"Description very short ({desc_len} chars)")
            if desc_len <= 5000:
                score += 5
            # Check first 125 chars (shown in search)
            first_line = description[:125]
            if len(first_line.split()) >= 10:
                score += 10
            else:
                feedback.append("First 125 chars of description too thin (shown in search results)")
        else:
            feedback.append("Missing description")

        # Tags checks (0-30 points)
        if tags:
            tag_count = len(tags)
            if tag_count >= 10:
                score += 15
            elif tag_count >= 5:
                score += 8
            else:
                feedback.append(f"Only {tag_count} tags (aim for 10-15)")
            # Check for long-tail tags
            long_tail = [t for t in tags if len(t.split()) >= 3]
            if len(long_tail) >= 3:
                score += 10
            else:
                feedback.append("Add more long-tail keyword tags (3+ words)")
            # Check for duplicates
            if len(set(t.lower() for t in tags)) == len(tags):
                score += 5
            else:
                feedback.append("Duplicate tags detected")
        else:
            feedback.append("No tags")

        # Hashtag bonus (0-10)
        if description and "#" in description:
            score += 5
        hashtag_count = description.count("#") if description else 0
        if 3 <= hashtag_count <= 15:
            score += 5
        elif hashtag_count > 15:
            feedback.append("Too many hashtags (keep 3-15)")

        return {
            "score": min(score, 100),
            "feedback": feedback,
            "grade": "A" if score >= 80 else "B" if score >= 60 else "C" if score >= 40 else "D",
        }
