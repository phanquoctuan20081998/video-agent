"""
Trending Content Search Module
"""

import httpx
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
            self.logger.error(f"Error searching YouTube trending: {e}")
            return []
    
    async def search_tiktok_trending(
        self,
        keywords: Optional[list[str]] = None,
        max_results: int = 10
    ) -> List[TrendingContent]:
        """
        Search TikTok trending videos
        Uses web scraping (requires selenium)
        """
        try:
            from selenium import webdriver
            from selenium.webdriver.common.by import By
            from selenium.webdriver.support.ui import WebDriverWait
            from selenium.webdriver.support import expected_conditions as EC
            
            driver = webdriver.Chrome()
            driver.get("https://www.tiktok.com/discover")
            
            # Wait for content to load
            WebDriverWait(driver, 10).until(
                EC.presence_of_all_elements_located((By.CLASS_NAME, "video-feed-item"))
            )
            
            results = []
            # Extract trending content (simplified)
            # In production, use proper TikTok API
            
            driver.quit()
            return results
            
        except Exception as e:
            self.logger.error(f"Error searching TikTok trending: {e}")
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
            self.logger.error(f"Error searching YouTube by topic: {e}")
            return []
