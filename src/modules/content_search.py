"""
Trending Content Search Module
"""

import httpx
import re
from typing import Optional, List
from datetime import datetime
from pydantic import BaseModel
from loguru import logger


class TrendingContent(BaseModel):
    """Trending content item"""
    title: str
    description: str
    views: int
    likes: int
    keywords: list[str]
    url: Optional[str] = None
    source: str
    category: str
    trending_score: float


class ContentSearcher:
    """Search for trending and viral content"""
    
    def __init__(self):
        self.logger = logger
        self.youtube_quota_exceeded = False

    @staticmethod
    def _safe_api_error(error: Exception) -> str:
        text = str(error)
        text = re.sub(r"([?&]key=)[^&\s]+", r"\1<redacted>", text)
        if "Quota exceeded" in text:
            return "YouTube API quota exceeded for search queries"
        if len(text) > 500:
            text = f"{text[:500]}..."
        return text
    
    async def search_youtube_trending(
        self,
        category: str = "all",
        region: str = "US",
        max_results: int = 10
    ) -> List[TrendingContent]:
        """
        Search YouTube trending videos
        Requires: YOUTUBE_DEVELOPER_KEY from environment
        """
        from src.core import config
        
        api_key = config.settings.youtube_developer_key
        if not api_key:
            self.logger.error("YouTube API key not configured")
            return []
        
        try:
            from googleapiclient.discovery import build
            
            youtube = build("youtube", "v3", developerKey=api_key)
            
            request = youtube.videos().list(
                part="snippet,statistics",
                chart="mostPopular",
                regionCode=region,
                maxResults=max_results,
                videoCategoryId=category if category != "all" else None
            )
            
            response = request.execute()
            
            results = []
            for item in response.get("items", []):
                snippet = item.get("snippet", {})
                stats = item.get("statistics", {})
                
                content = TrendingContent(
                    title=snippet.get("title", ""),
                    description=snippet.get("description", "")[:200],
                    views=int(stats.get("viewCount", 0)),
                    likes=int(stats.get("likeCount", 0)),
                    keywords=snippet.get("tags", []),
                    url=f"https://www.youtube.com/watch?v={item['id']}",
                    source="youtube",
                    category=snippet.get("categoryId", ""),
                    trending_score=float(stats.get("viewCount", 0)) * 0.6 + 
                                   float(stats.get("likeCount", 0)) * 0.4
                )
                results.append(content)
            
            self.logger.info(f"Found {len(results)} trending videos on YouTube")
            return sorted(results, key=lambda x: x.trending_score, reverse=True)
            
        except Exception as e:
            message = self._safe_api_error(e)
            if "quota exceeded" in message.lower():
                self.youtube_quota_exceeded = True
                self.logger.warning(f"Error searching YouTube trending: {message}")
            else:
                self.logger.error(f"Error searching YouTube trending: {message}")
            return []
    
    async def fetch_google_trends(self, geo: str = "VN", max_results: int = 20) -> list[str]:
        """Fetch today's trending search terms from Google Trends' public RSS feed.
        No API key needed. Returns plain search terms (not full TrendingContent —
        this feed has no view/like counts, just what people are searching right now)."""
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(
                    "https://trends.google.com/trending/rss",
                    params={"geo": geo},
                )
                resp.raise_for_status()
                import xml.etree.ElementTree as ET
                root = ET.fromstring(resp.text)
                terms = [
                    item.findtext("title", default="").strip()
                    for item in root.findall(".//item")
                ]
                terms = [t for t in terms if t][:max_results]
                self.logger.info(f"Found {len(terms)} Google Trends terms for geo={geo}")
                return terms
        except Exception as e:
            self.logger.warning(f"Error fetching Google Trends for geo={geo}: {e}")
            return []

    async def search_reddit_hot(
        self,
        subreddit: str,
        max_results: int = 10
    ) -> List[TrendingContent]:
        """Fetch hot posts from a subreddit via Reddit's app-only OAuth flow.
        Requires REDDIT_CLIENT_ID + REDDIT_CLIENT_SECRET (free, create a "script"
        app at reddit.com/prefs/apps)."""
        from src.core import config

        client_id = config.settings.reddit_client_id
        client_secret = config.settings.reddit_client_secret
        if not client_id or not client_secret:
            self.logger.debug("Reddit credentials not configured; skipping")
            return []

        user_agent = "video-agent-trend-research/1.0"
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                token_resp = await client.post(
                    "https://www.reddit.com/api/v1/access_token",
                    data={"grant_type": "client_credentials"},
                    auth=(client_id, client_secret),
                    headers={"User-Agent": user_agent},
                )
                token_resp.raise_for_status()
                access_token = token_resp.json().get("access_token")
                if not access_token:
                    return []

                posts_resp = await client.get(
                    f"https://oauth.reddit.com/r/{subreddit}/hot",
                    params={"limit": max_results},
                    headers={
                        "Authorization": f"bearer {access_token}",
                        "User-Agent": user_agent,
                    },
                )
                posts_resp.raise_for_status()
                children = posts_resp.json().get("data", {}).get("children", [])

                results = []
                for child in children:
                    post = child.get("data", {})
                    if post.get("stickied"):
                        continue
                    results.append(TrendingContent(
                        title=post.get("title", ""),
                        description=(post.get("selftext") or "")[:200],
                        views=int(post.get("view_count") or 0),
                        likes=int(post.get("score") or 0),
                        keywords=[subreddit],
                        url=f"https://reddit.com{post.get('permalink', '')}",
                        source="reddit",
                        category=subreddit,
                        trending_score=float(post.get("score") or 0) * (1 + float(post.get("upvote_ratio") or 0)),
                    ))
                self.logger.info(f"Found {len(results)} hot posts on r/{subreddit}")
                return results
        except Exception as e:
            self.logger.warning(f"Error fetching Reddit hot posts for r/{subreddit}: {e}")
            return []

    async def search_topic_on_youtube(
        self,
        topic: str,
        max_results: int = 20
    ) -> List[TrendingContent]:
        """Search YouTube for specific topic"""
        from src.core import config
        
        api_key = config.settings.youtube_developer_key
        if not api_key:
            self.logger.error("YouTube API key not configured")
            return []
        
        try:
            from googleapiclient.discovery import build
            
            youtube = build("youtube", "v3", developerKey=api_key)
            
            request = youtube.search().list(
                part="snippet",
                q=topic,
                type="video",
                maxResults=max_results,
                order="viewCount",
                relevanceLanguage="en"
            )
            
            response = request.execute()
            
            results = []
            video_ids = [item["id"]["videoId"] for item in response.get("items", [])]
            
            # Get statistics for videos
            if video_ids:
                stats_request = youtube.videos().list(
                    part="statistics",
                    id=",".join(video_ids)
                )
                stats_response = stats_request.execute()
                
                for i, item in enumerate(response.get("items", [])):
                    snippet = item.get("snippet", {})
                    stats = stats_response["items"][i].get("statistics", {})
                    
                    content = TrendingContent(
                        title=snippet.get("title", ""),
                        description=snippet.get("description", "")[:200],
                        views=int(stats.get("viewCount", 0)),
                        likes=int(stats.get("likeCount", 0)),
                        keywords=[topic],
                        url=f"https://www.youtube.com/watch?v={item['id']['videoId']}",
                        source="youtube",
                        category="search",
                        trending_score=float(stats.get("viewCount", 0))
                    )
                    results.append(content)
            
            self.logger.info(f"Found {len(results)} videos for topic: {topic}")
            return results
            
        except Exception as e:
            message = self._safe_api_error(e)
            if "quota exceeded" in message.lower():
                self.youtube_quota_exceeded = True
                self.logger.warning(f"Error searching YouTube by topic '{topic}': {message}")
            else:
                self.logger.error(f"Error searching YouTube by topic '{topic}': {message}")
            return []
