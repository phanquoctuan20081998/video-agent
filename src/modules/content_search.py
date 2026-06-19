"""
Trending Content Search Module
"""

import asyncio
import httpx
import re
from typing import Optional, List
from datetime import datetime, timezone
from urllib.parse import quote_plus
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
    published_at: Optional[datetime] = None
    engagement_rate: float = 0.0     # likes/views
    velocity: float = 0.0            # views per hour since publish
    comments: int = 0


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
                views = int(stats.get("viewCount", 0))
                likes = int(stats.get("likeCount", 0))
                comments = int(stats.get("commentCount", 0))
                published = snippet.get("publishedAt", "")
                pub_dt = self._parse_yt_date(published)
                velocity = self._calc_velocity(views, pub_dt)
                eng_rate = (likes / views) if views > 0 else 0.0

                content = TrendingContent(
                    title=snippet.get("title", ""),
                    description=snippet.get("description", "")[:200],
                    views=views,
                    likes=likes,
                    comments=comments,
                    keywords=snippet.get("tags", []) or [],
                    url=f"https://www.youtube.com/watch?v={item['id']}",
                    source="youtube",
                    category=snippet.get("categoryId", ""),
                    published_at=pub_dt,
                    engagement_rate=eng_rate,
                    velocity=velocity,
                    trending_score=self._calc_trending_score(views, likes, comments, velocity, eng_rate),
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

    async def fetch_google_trends_related(
        self,
        topic: str,
        geo: str = "VN",
    ) -> dict:
        """Fetch Google Trends related queries and topics for a SPECIFIC topic.
        Uses the Google Trends explore endpoint to find what people search
        around this topic — much more useful than generic hot searches."""
        try:
            import json as _json
            async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
                # Step 1: Get token for the topic
                token_resp = await client.get(
                    "https://trends.google.com/trends/api/explore",
                    params={
                        "hl": "en-US",
                        "tz": "-420",
                        "req": _json.dumps({
                            "comparisonItem": [{"keyword": topic, "geo": geo, "time": "today 3-m"}],
                            "category": 0,
                            "property": "",
                        }),
                    },
                    headers={
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    },
                )
                if token_resp.status_code != 200:
                    self.logger.debug(f"Google Trends explore returned {token_resp.status_code}")
                    return {"related_queries": [], "related_topics": []}

                # Response has )]}' prefix
                text = token_resp.text
                if text.startswith(")]}'"):
                    text = text[5:]
                explore_data = _json.loads(text)
                widgets = explore_data.get("widgets", [])

                related_queries = []
                related_topics = []

                for widget in widgets:
                    widget_id = widget.get("id", "")
                    token = widget.get("token", "")
                    req = widget.get("request", {})

                    if "RELATED_QUERIES" in widget_id and token:
                        rq_resp = await client.get(
                            "https://trends.google.com/trends/api/widgetdata/relatedsearches",
                            params={
                                "hl": "en-US",
                                "tz": "-420",
                                "req": _json.dumps(req),
                                "token": token,
                            },
                            headers={
                                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                            },
                        )
                        if rq_resp.status_code == 200:
                            rq_text = rq_resp.text
                            if rq_text.startswith(")]}'"):
                                rq_text = rq_text[5:]
                            rq_data = _json.loads(rq_text)
                            # Rising queries
                            for block in rq_data.get("default", {}).get("rankedList", []):
                                for item in block.get("rankedKeyword", []):
                                    q = item.get("query", "")
                                    val = item.get("formattedValue", "")
                                    if q:
                                        related_queries.append({"query": q, "growth": val})

                    elif "RELATED_TOPICS" in widget_id and token:
                        rt_resp = await client.get(
                            "https://trends.google.com/trends/api/widgetdata/relatedsearches",
                            params={
                                "hl": "en-US",
                                "tz": "-420",
                                "req": _json.dumps(req),
                                "token": token,
                            },
                            headers={
                                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                            },
                        )
                        if rt_resp.status_code == 200:
                            rt_text = rt_resp.text
                            if rt_text.startswith(")]}'"):
                                rt_text = rt_text[5:]
                            rt_data = _json.loads(rt_text)
                            for block in rt_data.get("default", {}).get("rankedList", []):
                                for item in block.get("rankedKeyword", []):
                                    t = item.get("topic", {})
                                    if t:
                                        related_topics.append({
                                            "title": t.get("title", ""),
                                            "type": t.get("type", ""),
                                            "growth": item.get("formattedValue", ""),
                                        })

                self.logger.info(
                    f"Google Trends related: {len(related_queries)} queries, "
                    f"{len(related_topics)} topics for '{topic}' in {geo}"
                )
                return {
                    "related_queries": related_queries[:20],
                    "related_topics": related_topics[:15],
                }
        except Exception as e:
            self.logger.warning(f"Error fetching Google Trends related for '{topic}': {e}")
            return {"related_queries": [], "related_topics": []}

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
        max_results: int = 20,
        order: str = "relevance",
        published_after: Optional[str] = None,
    ) -> List[TrendingContent]:
        """Search YouTube for specific topic. order: relevance|date|viewCount|rating"""
        from src.core import config
        
        api_key = config.settings.youtube_developer_key
        if not api_key:
            self.logger.error("YouTube API key not configured")
            return []
        
        try:
            from googleapiclient.discovery import build
            
            youtube = build("youtube", "v3", developerKey=api_key)
            
            search_params = dict(
                part="snippet",
                q=topic,
                type="video",
                maxResults=max_results,
                order=order,
            )
            if published_after:
                search_params["publishedAfter"] = published_after
            
            response = youtube.search().list(**search_params).execute()
            
            results = []
            video_ids = [item["id"]["videoId"] for item in response.get("items", []) if item.get("id", {}).get("videoId")]
            
            if video_ids:
                stats_response = youtube.videos().list(
                    part="snippet,statistics",
                    id=",".join(video_ids)
                ).execute()
                
                stats_map = {v["id"]: v for v in stats_response.get("items", [])}
                
                for item in response.get("items", []):
                    vid_id = item.get("id", {}).get("videoId")
                    if not vid_id or vid_id not in stats_map:
                        continue
                    snippet = stats_map[vid_id].get("snippet", {})
                    stats = stats_map[vid_id].get("statistics", {})
                    views = int(stats.get("viewCount", 0))
                    likes = int(stats.get("likeCount", 0))
                    comments = int(stats.get("commentCount", 0))
                    pub_dt = self._parse_yt_date(snippet.get("publishedAt", ""))
                    velocity = self._calc_velocity(views, pub_dt)
                    eng_rate = (likes / views) if views > 0 else 0.0
                    
                    content = TrendingContent(
                        title=snippet.get("title", ""),
                        description=snippet.get("description", "")[:200],
                        views=views,
                        likes=likes,
                        comments=comments,
                        keywords=snippet.get("tags", []) or [topic],
                        url=f"https://www.youtube.com/watch?v={vid_id}",
                        source="youtube",
                        category="search",
                        published_at=pub_dt,
                        engagement_rate=eng_rate,
                        velocity=velocity,
                        trending_score=self._calc_trending_score(views, likes, comments, velocity, eng_rate),
                    )
                    results.append(content)
            
            self.logger.info(f"Found {len(results)} videos for topic: {topic}")
            return sorted(results, key=lambda x: x.trending_score, reverse=True)
            
        except Exception as e:
            message = self._safe_api_error(e)
            if "quota exceeded" in message.lower():
                self.youtube_quota_exceeded = True
                self.logger.warning(f"Error searching YouTube by topic '{topic}': {message}")
            else:
                self.logger.error(f"Error searching YouTube by topic '{topic}': {message}")
            return []

    # ── Helper methods ────────────────────────────────────────────

    @staticmethod
    def _parse_yt_date(date_str: str) -> Optional[datetime]:
        if not date_str:
            return None
        try:
            return datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        except Exception:
            return None

    @staticmethod
    def _calc_velocity(views: int, published_at: Optional[datetime]) -> float:
        if not published_at:
            return 0.0
        now = datetime.now(timezone.utc)
        hours = max((now - published_at).total_seconds() / 3600, 1)
        return views / hours

    @staticmethod
    def _calc_trending_score(
        views: int, likes: int, comments: int,
        velocity: float, engagement_rate: float,
    ) -> float:
        """Weighted score: velocity matters most (catches rising content),
        engagement rate second (filters clickbait), raw views last."""
        import math
        v_score = math.log10(max(velocity, 1)) * 30          # rising fast = high
        e_score = engagement_rate * 200                        # high like-ratio = quality
        c_score = math.log10(max(comments, 1)) * 10           # comments = discussion
        raw_score = math.log10(max(views, 1)) * 5             # baseline popularity
        return v_score + e_score + c_score + raw_score

    # ── YouTube Rising (recent + fast-growing) ───────────────────

    async def search_youtube_rising(
        self,
        topic: str,
        hours: int = 48,
        max_results: int = 15,
    ) -> List[TrendingContent]:
        """Find videos published in last `hours` hours with highest velocity.
        These are the 'about-to-blow-up' videos — uploaded recently, gaining views fast."""
        from datetime import timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
        results = await self.search_topic_on_youtube(
            topic=topic,
            max_results=max_results,
            order="relevance",
            published_after=cutoff,
        )
        # re-sort by velocity (the whole point of this method)
        return sorted(results, key=lambda x: x.velocity, reverse=True)

    # ── TikTok Creative Center (no API key needed) ───────────────

    async def fetch_tiktok_trending(
        self,
        region: str = "VN",
        max_results: int = 20,
    ) -> List[TrendingContent]:
        """Fetch TikTok trending hashtags via Creative Center APIs.
        Tries multiple endpoints since TikTok rotates them."""
        endpoints = [
            {
                "url": "https://ads.tiktok.com/creative_radar_api/v1/popular_trend/hashtag/list",
                "params": {
                    "page": 1, "limit": max_results,
                    "period": 7, "country_code": region, "sort_by": "popular",
                },
                "list_path": ["data", "list"],
                "name_key": "hashtag_name",
                "count_key": "publish_cnt",
                "trend_key": "trend",
            },
            {
                "url": "https://ads.tiktok.com/creative_radar_api/v1/popular_trend/hashtag/list",
                "params": {
                    "page": 1, "limit": max_results,
                    "period": 30, "country_code": region, "sort_by": "popular",
                },
                "list_path": ["data", "list"],
                "name_key": "hashtag_name",
                "count_key": "publish_cnt",
                "trend_key": "trend",
            },
            {
                "url": "https://ads.tiktok.com/creative_radar_api/v1/popular_trend/creator/list",
                "params": {
                    "page": 1, "limit": max_results,
                    "period": 7, "country_code": region,
                },
                "list_path": ["data", "list"],
                "name_key": "nickname",
                "count_key": "follower_cnt",
                "trend_key": "follower_cnt",
            },
        ]

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://ads.tiktok.com/business/creativecenter/inspiration/popular/hashtag/pc/en",
        }

        for ep in endpoints:
            try:
                async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
                    resp = await client.get(ep["url"], params=ep["params"], headers=headers)
                    if resp.status_code != 200:
                        self.logger.debug(
                            f"TikTok endpoint {ep['url']} returned {resp.status_code}"
                        )
                        continue

                    data = resp.json()
                    # Navigate to list using path
                    items = data
                    for key in ep["list_path"]:
                        items = items.get(key, {}) if isinstance(items, dict) else []
                    if not isinstance(items, list) or not items:
                        self.logger.debug(
                            f"TikTok endpoint returned empty list. Response keys: "
                            f"{list(data.keys()) if isinstance(data, dict) else 'not-dict'}, "
                            f"code={data.get('code')}, msg={data.get('msg', '')}"
                        )
                        continue

                    results = []
                    for item in items:
                        name = item.get(ep["name_key"], "")
                        if not name:
                            continue
                        count = int(item.get(ep["count_key"], 0) or 0)
                        trend_val = float(item.get(ep["trend_key"], 0) or 0)
                        results.append(TrendingContent(
                            title=f"#{name}" if ep["name_key"] == "hashtag_name" else name,
                            description=f"TikTok trending — {count:,} posts/followers",
                            views=count,
                            likes=0,
                            keywords=[name],
                            url=f"https://www.tiktok.com/tag/{name}",
                            source="tiktok",
                            category="hashtag",
                            trending_score=trend_val * 100 + count,
                        ))
                    if results:
                        self.logger.info(
                            f"Found {len(results)} TikTok trending items for {region}"
                        )
                        return sorted(
                            results, key=lambda x: x.trending_score, reverse=True
                        )
            except Exception as e:
                self.logger.debug(f"TikTok endpoint {ep['url']} failed: {e}")
                continue

        # Fallback: scrape TikTok Discover page for trending topics
        try:
            async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
                resp = await client.get(
                    "https://www.tiktok.com/api/discover/",
                    params={"from_page": "search", "region": region},
                    headers=headers,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    results = []
                    for cat in data.get("data", []):
                        for item in cat.get("list", []):
                            title = item.get("title", "") or item.get("desc", "")
                            if title:
                                results.append(TrendingContent(
                                    title=title,
                                    description=f"TikTok discover topic",
                                    views=0, likes=0, keywords=[title],
                                    url="https://www.tiktok.com/search?q=" + quote_plus(title),
                                    source="tiktok", category="discover",
                                    trending_score=50.0,
                                ))
                    if results:
                        self.logger.info(
                            f"Found {len(results)} TikTok discover topics for {region}"
                        )
                        return results[:max_results]
        except Exception as e:
            self.logger.debug(f"TikTok discover fallback failed: {e}")

        self.logger.warning(f"All TikTok endpoints returned no data for {region}")
        return []

    # ── X (Twitter) Trending ───────────────────────────────────

    async def fetch_x_trending(
        self,
        topic: Optional[str] = None,
        max_results: int = 20,
    ) -> List[TrendingContent]:
        """Fetch trending/viral posts from X (Twitter).
        Uses v2 search/recent endpoint with sort_order=relevancy.
        Requires X_BEARER_TOKEN from environment."""
        from src.core import config

        bearer = config.settings.x_bearer_token
        if not bearer:
            self.logger.debug("X bearer token not configured; skipping")
            return []

        try:
            query = topic if topic else "viral OR trending"
            # Filter: min engagement, has media, not retweets
            search_query = f"{query} min_faves:1000 -is:retweet has:media lang:en"

            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(
                    "https://api.x.com/2/tweets/search/recent",
                    params={
                        "query": search_query,
                        "max_results": min(max_results, 100),
                        "sort_order": "relevancy",
                        "tweet.fields": "public_metrics,created_at,entities",
                        "expansions": "author_id",
                        "user.fields": "username",
                    },
                    headers={"Authorization": f"Bearer {bearer}"},
                )
                if resp.status_code == 429:
                    self.logger.debug("X API rate limited")
                    return []
                resp.raise_for_status()
                data = resp.json()

            tweets = data.get("data", [])
            results = []
            for tw in tweets:
                metrics = tw.get("public_metrics", {})
                likes = int(metrics.get("like_count", 0))
                retweets = int(metrics.get("retweet_count", 0))
                replies = int(metrics.get("reply_count", 0))
                impressions = int(metrics.get("impression_count", 0)) or 1
                pub_dt = self._parse_yt_date(tw.get("created_at", ""))
                velocity = self._calc_velocity(likes + retweets, pub_dt)
                eng_rate = (likes + retweets + replies) / impressions

                hashtags = []
                for ent in (tw.get("entities", {}).get("hashtags") or []):
                    hashtags.append(ent.get("tag", ""))

                results.append(TrendingContent(
                    title=tw.get("text", "")[:120],
                    description=tw.get("text", "")[:200],
                    views=impressions,
                    likes=likes,
                    comments=replies,
                    keywords=hashtags[:5] or ([topic] if topic else []),
                    url=f"https://x.com/i/status/{tw['id']}",
                    source="x",
                    category="tweet",
                    published_at=pub_dt,
                    engagement_rate=eng_rate,
                    velocity=velocity,
                    trending_score=self._calc_trending_score(
                        impressions, likes, replies, velocity, eng_rate
                    ),
                ))

            self.logger.info(f"Found {len(results)} viral tweets on X")
            return sorted(results, key=lambda x: x.trending_score, reverse=True)
        except Exception as e:
            self.logger.debug(f"X trending fetch failed: {e}")
            return []

    # ── Facebook Trending ─────────────────────────────────────────

    async def fetch_facebook_trending(
        self,
        pages: Optional[List[str]] = None,
        max_results: int = 20,
    ) -> List[TrendingContent]:
        """Fetch high-performing recent posts from public Facebook pages.
        Uses Graph API /page/feed. Requires FACEBOOK_ACCESS_TOKEN.
        `pages` = list of page IDs or slugs to monitor."""
        from src.core import config

        token = config.settings.facebook_access_token
        if not token:
            self.logger.debug("Facebook access token not configured; skipping")
            return []

        if pages is None:
            # Default viral/trending pages to monitor
            pages = ["NowThisNews", "LADbible", "UNILAD", "ViralThread"]

        results = []
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                for page in pages[:8]:  # cap to avoid rate limits
                    resp = await client.get(
                        f"https://graph.facebook.com/v19.0/{page}/feed",
                        params={
                            "access_token": token,
                            "fields": "message,created_time,shares,reactions.summary(true).limit(0),comments.summary(true).limit(0)",
                            "limit": max_results // len(pages) + 1,
                        },
                    )
                    if resp.status_code != 200:
                        continue
                    posts = resp.json().get("data", [])
                    for post in posts:
                        msg = post.get("message", "")
                        if not msg:
                            continue
                        reactions = int(post.get("reactions", {}).get("summary", {}).get("total_count", 0))
                        comments = int(post.get("comments", {}).get("summary", {}).get("total_count", 0))
                        shares = int((post.get("shares") or {}).get("count", 0))
                        pub_dt = self._parse_yt_date(
                            post.get("created_time", "").replace("+0000", "+00:00")
                        )
                        velocity = self._calc_velocity(reactions + shares, pub_dt)
                        total_engagement = reactions + comments + shares
                        eng_rate = total_engagement / max(reactions, 1)

                        results.append(TrendingContent(
                            title=msg[:120],
                            description=msg[:200],
                            views=reactions + shares,  # no view count in Graph API
                            likes=reactions,
                            comments=comments,
                            keywords=[page],
                            url=f"https://facebook.com/{post.get('id', '')}",
                            source="facebook",
                            category="post",
                            published_at=pub_dt,
                            engagement_rate=eng_rate,
                            velocity=velocity,
                            trending_score=self._calc_trending_score(
                                reactions + shares, reactions, comments, velocity, min(eng_rate, 1.0)
                            ),
                        ))

            self.logger.info(f"Found {len(results)} Facebook trending posts")
            return sorted(results, key=lambda x: x.trending_score, reverse=True)
        except Exception as e:
            self.logger.debug(f"Facebook trending fetch failed: {e}")
            return []

    # ── Web search for niche/topic research ──────────────────────

    async def search_web_for_topic(
        self,
        topic: str,
        max_results: int = 15,
    ) -> list[dict]:
        """Search Google/DuckDuckGo for topic-specific content: what videos exist,
        what angles are popular, what facts people search for.
        Uses helpers/web_research.py logic but async-compatible."""
        queries = [
            f"{topic} most viewed YouTube videos",
            f"{topic} viral video ideas",
            f"{topic} interesting facts most people don't know",
            f"{topic} surprising statistics 2025 2026",
            f"{topic} YouTube channel best performing",
            f'"{topic}" why explained fascinating',
            f"{topic} reddit best posts all time",
            f"{topic} quora what are the most interesting",
        ]

        all_results = []
        seen_urls = set()

        # Try Google Custom Search first, fallback to DuckDuckGo
        from src.core import config
        google_key = getattr(config.settings, "google_search_api_key", "")
        google_cx = getattr(config.settings, "google_search_cx", "")

        async with httpx.AsyncClient(timeout=15) as client:
            for q in queries:
                try:
                    if google_key and google_cx:
                        resp = await client.get(
                            "https://www.googleapis.com/customsearch/v1",
                            params={"key": google_key, "cx": google_cx, "q": q, "num": 5},
                        )
                        if resp.status_code == 200:
                            for item in resp.json().get("items", []):
                                url = item.get("link", "")
                                if url not in seen_urls:
                                    seen_urls.add(url)
                                    all_results.append({
                                        "query": q,
                                        "title": item.get("title", ""),
                                        "snippet": item.get("snippet", ""),
                                        "url": url,
                                        "source": item.get("displayLink", ""),
                                    })
                            continue

                    # DuckDuckGo fallback
                    resp = await client.get(
                        "https://html.duckduckgo.com/html/",
                        params={"q": q},
                        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
                        follow_redirects=True,
                    )
                    if resp.status_code == 200:
                        import re as _re
                        blocks = _re.findall(
                            r'<a rel="nofollow" class="result__a" href="([^"]+)"[^>]*>(.*?)</a>.*?'
                            r'<a class="result__snippet"[^>]*>(.*?)</a>',
                            resp.text, _re.DOTALL,
                        )
                        for url, title, snippet in blocks[:5]:
                            title = _re.sub(r"<[^>]+>", "", title).strip()
                            snippet = _re.sub(r"<[^>]+>", "", snippet).strip()
                            if url not in seen_urls:
                                seen_urls.add(url)
                                all_results.append({
                                    "query": q,
                                    "title": title,
                                    "snippet": snippet,
                                    "url": url,
                                    "source": url.split("/")[2] if "/" in url else "",
                                })
                except Exception as e:
                    self.logger.debug(f"Web search failed for '{q}': {e}")

        self.logger.info(f"Web search found {len(all_results)} results for topic '{topic}'")
        return all_results[:max_results]

    # ── Topic-relevant subreddit picker ──────────────────────────

    TOPIC_SUBREDDIT_MAP = {
        "geography": ["geography", "MapPorn", "geopolitics", "dataisbeautiful"],
        "geo": ["geography", "MapPorn", "geopolitics"],
        "map": ["MapPorn", "geography", "dataisbeautiful"],
        "country": ["geography", "geopolitics", "worldnews", "MapPorn"],
        "science": ["science", "todayilearned", "Futurology"],
        "history": ["history", "todayilearned", "AskHistorians"],
        "space": ["space", "Astronomy", "todayilearned"],
        "nature": ["NatureIsFuckingLit", "EarthPorn", "Damnthatsinteresting"],
        "animal": ["NatureIsFuckingLit", "Awwducational", "todayilearned"],
        "tech": ["technology", "Futurology", "gadgets"],
        "food": ["food", "Cooking", "todayilearned"],
        "travel": ["travel", "solotravel", "backpacking"],
    }

    def _pick_subreddits(self, topic: str) -> list[str]:
        """Auto-pick relevant subreddits based on topic keywords."""
        topic_lower = topic.lower()
        picked = set()
        for keyword, subs in self.TOPIC_SUBREDDIT_MAP.items():
            if keyword in topic_lower:
                picked.update(subs)
        # Always include general viral subs
        picked.update(["Damnthatsinteresting", "todayilearned"])
        return list(picked)[:6]

    # ── Cross-platform viral research ────────────────────────────

    async def research_viral_topics(
        self,
        topic: str,
        region: str = "VN",
        subreddits: Optional[List[str]] = None,
    ) -> dict:
        """Aggregate TOPIC-SPECIFIC data across all platforms.
        Focuses entirely on THIS topic/niche — no generic trending noise."""
        if subreddits is None:
            subreddits = self._pick_subreddits(topic)

        # Generate multiple search variations for better coverage
        topic_variations = [topic]
        topic_lower = topic.lower()
        if "geography" in topic_lower:
            topic_variations.extend([
                "geography facts most people don't know",
                "geography why countries shaped",
                "geography comparison countries",
                "geographic anomalies explained",
            ])
        elif "history" in topic_lower:
            topic_variations.extend(["history facts nobody knows", "historical mysteries"])
        elif "science" in topic_lower:
            topic_variations.extend(["science experiments amazing", "scientific discoveries"])

        # ── Topic-specific research (ALL that matters) ──
        topic_tasks = [
            self.search_topic_on_youtube(topic=v, max_results=10, order="relevance")
            for v in topic_variations[:3]
        ]
        topic_tasks.extend([
            self.search_topic_on_youtube(topic=topic, max_results=10, order="date"),
            self.search_youtube_rising(topic=topic, hours=72, max_results=15),
            self.fetch_google_trends_related(topic=topic, geo=region),
            self.search_web_for_topic(topic=topic, max_results=20),
        ])

        # ── Topic-relevant subreddits ──
        reddit_tasks = [self.search_reddit_hot(subreddit=sub, max_results=10) for sub in subreddits]

        all_tasks = topic_tasks + reddit_tasks
        results = await asyncio.gather(*all_tasks, return_exceptions=True)

        def _safe(idx):
            r = results[idx] if idx < len(results) else []
            return r if not isinstance(r, Exception) else ([] if not isinstance(r, dict) else {})

        # Merge all YT topic search results (deduplicated)
        yt_all = []
        seen_yt_urls = set()
        n_variations = min(len(topic_variations), 3)
        for i in range(n_variations):
            items = _safe(i)
            if isinstance(items, list):
                for item in items:
                    if hasattr(item, 'url') and item.url not in seen_yt_urls:
                        seen_yt_urls.add(item.url)
                        yt_all.append(item)

        # Recent uploads (by date)
        yt_recent = _safe(n_variations) or []
        if isinstance(yt_recent, list):
            for item in yt_recent:
                if hasattr(item, 'url') and item.url not in seen_yt_urls:
                    seen_yt_urls.add(item.url)
                    yt_all.append(item)

        yt_rising = _safe(n_variations + 1) or []
        google_related = _safe(n_variations + 2)
        if not isinstance(google_related, dict):
            google_related = {"related_queries": [], "related_topics": []}
        web_results = _safe(n_variations + 3) or []

        # Reddit
        reddit_base = len(topic_tasks)
        reddit_all = []
        for i in range(len(subreddits)):
            r = _safe(reddit_base + i)
            if isinstance(r, list):
                reddit_all.extend(r)

        def _top(items, n=10):
            if isinstance(items, list) and items and hasattr(items[0], 'trending_score'):
                return [
                    {"title": c.title, "views": c.views, "likes": c.likes,
                     "velocity": round(c.velocity, 1), "engagement": round(c.engagement_rate, 4),
                     "url": c.url, "keywords": c.keywords[:5]}
                    for c in sorted(items, key=lambda x: x.trending_score, reverse=True)[:n]
                ]
            return items[:n] if isinstance(items, list) else []

        report = {
            "query_topic": topic,
            "region": region,
            "niche_focus": True,
            "collected_at": datetime.now(timezone.utc).isoformat(),
            # ── NICHE RESEARCH (the only thing that matters) ──
            "competitor_videos": _top(yt_all, 15),
            "rising_in_niche": _top(yt_rising, 10),
            "google_related_searches": google_related,
            "web_research": web_results[:20],
            "reddit_niche_posts": _top(reddit_all, 10),
            "subreddits_used": subreddits,
            "search_variations_used": topic_variations[:3],
        }
        self.logger.info(
            f"Niche research complete: {len(yt_all)} competitor videos, "
            f"{len(yt_rising)} rising, {len(web_results)} web results, "
            f"{len(reddit_all)} reddit posts, "
            f"google_related={len(google_related.get('related_queries', []))} queries"
        )
        return report
