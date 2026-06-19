"""
Stock Video Fetcher Module
Fetch videos from Pexels, Pixabay, Coverr, YouTube (Creative Commons only)
"""

import asyncio
import os
import re
import time
import httpx
from typing import List, Optional
from pydantic import BaseModel
from loguru import logger


def _parse_iso8601_duration(value: str) -> float:
    """Parse ISO 8601 duration (e.g. PT1H2M3S) to seconds."""
    match = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", value or "")
    if not match:
        return 0.0
    hours, minutes, seconds = (int(g) if g else 0 for g in match.groups())
    return float(hours * 3600 + minutes * 60 + seconds)


class StockVideo(BaseModel):
    """Stock video item"""
    id: str
    title: str
    url: str
    preview_url: Optional[str] = None
    download_url: str
    duration: float
    width: int
    height: int
    source: str
    tags: list[str]


class StockImage(BaseModel):
    """Stock image item"""
    id: str
    title: str
    url: str
    preview_url: Optional[str] = None
    download_url: str
    width: int
    height: int
    source: str
    tags: list[str]


class StockVideoFetcher:
    """Fetch videos from various stock video sources"""
    
    def __init__(self):
        self.logger = logger
        self._youtube_cc_cache: dict[str, list[StockVideo]] = {}
        self._youtube_cc_cooldown_until = 0.0
    
    async def search_pexels(
        self,
        query: str,
        max_results: int = 20,
        min_duration: int = 5,
        max_duration: int = 60
    ) -> List[StockVideo]:
        """Search Pexels for stock videos"""
        from src.core import config
        
        api_key = config.settings.pexels_api_key
        if not api_key:
            self.logger.warning("Pexels API key not configured")
            return []
        
        try:
            headers = {"Authorization": api_key}
            params = {
                "query": query,
                "per_page": max_results,
                "min_duration": min_duration,
                "max_duration": max_duration
            }
            
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    "https://api.pexels.com/videos/search",
                    headers=headers,
                    params=params
                )
                response.raise_for_status()
                data = response.json()
            
            results = []
            for video in data.get("videos", []):
                # Get the best quality video file
                video_file = None
                for vf in video.get("video_files", []):
                    if vf.get("quality") == "hd":
                        video_file = vf
                        break
                
                if not video_file:
                    video_file = video.get("video_files", [{}])[0]
                
                if video_file:
                    stock_video = StockVideo(
                        id=str(video["id"]),
                        title=f"{query} - Pexels",
                        url=video.get("url", ""),
                        preview_url=video.get("image", ""),
                        download_url=video_file.get("link", ""),
                        duration=video.get("duration", 0),
                        width=video_file.get("width", 1920),
                        height=video_file.get("height", 1080),
                        source="pexels",
                        tags=[query]
                    )
                    results.append(stock_video)
            
            self.logger.info(f"Found {len(results)} videos on Pexels for: {query}")
            return results
            
        except Exception as e:
            self.logger.error(f"Error fetching from Pexels: {e}")
            return []
    
    async def search_pixabay(
        self,
        query: str,
        max_results: int = 20
    ) -> List[StockVideo]:
        """Search Pixabay for stock videos"""
        from src.core import config
        
        api_key = config.settings.pixabay_api_key
        if not api_key:
            self.logger.warning("Pixabay API key not configured")
            return []
        
        try:
            params = {
                "key": api_key,
                "q": query,
                "per_page": max(3, max_results),
                "video_type": "all",
                "order": "popular"
            }
            
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    "https://pixabay.com/api/videos/",
                    params=params
                )
                response.raise_for_status()
                data = response.json()
            
            results = []
            for video in data.get("hits", []):
                # Get the best quality video
                videos = video.get("videos", {})
                video_file = videos.get("large") or videos.get("medium")
                
                if video_file:
                    stock_video = StockVideo(
                        id=str(video["id"]),
                        title=f"{query} - Pixabay",
                        url=video.get("pageURL", ""),
                        preview_url=video.get("previewURL", ""),
                        download_url=video_file.get("url", ""),
                        duration=video.get("duration", 0),
                        width=video_file.get("width", 1920),
                        height=video_file.get("height", 1080),
                        source="pixabay",
                        tags=[query]
                    )
                    results.append(stock_video)
            
            self.logger.info(f"Found {len(results)} videos on Pixabay for: {query}")
            return results
            
        except Exception as e:
            self.logger.warning(f"Error fetching from Pixabay: {e}")
            return []
    
    async def search_coverr(
        self,
        query: str,
        max_results: int = 20
    ) -> List[StockVideo]:
        """Search Coverr for stock videos."""
        from src.core import config

        if not config.settings.enable_coverr:
            self.logger.debug("Coverr disabled; skipping")
            return []

        api_key = config.settings.coverr_api_key
        if not api_key:
            self.logger.warning("Coverr API key not configured")
            return []

        try:
            headers = {"Authorization": f"Bearer {api_key}"}
            params = {
                "query": query,
                "page_size": max(1, max_results),
                "page": 0,
                "urls": "true",  # urls object (mp4/mp4_download) omitted unless requested
            }

            async with httpx.AsyncClient() as client:
                response = await client.get(
                    "https://api.coverr.co/videos",
                    headers=headers,
                    params=params
                )
                response.raise_for_status()
                data = response.json()
            
            results = []
            for video in data.get("hits", []):
                urls = video.get("urls") or {}
                download_url = urls.get("mp4_download") or urls.get("mp4") or urls.get("mp4_preview") or ""
                if not download_url:
                    continue
                tags = video.get("search_keywords") or []
                stock_video = StockVideo(
                    id=str(video.get("id") or video.get("objectID") or download_url),
                    title=video.get("title") or f"{query} - Coverr",
                    url=f"https://coverr.co/videos/{video.get('slug')}" if video.get("slug") else "",
                    preview_url=video.get("thumbnail") or video.get("poster") or urls.get("mp4_preview"),
                    download_url=download_url,
                    duration=float(video.get("duration") or 0),
                    width=int(video.get("max_width") or 1920),
                    height=int(video.get("max_height") or 1080),
                    source="coverr",
                    tags=[query, *tags[:10]],
                )
                results.append(stock_video)
            
            self.logger.info(f"Found {len(results)} videos on Coverr for: {query}")
            return results[:max_results]
            
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                self.logger.warning(
                    "Coverr API endpoint returned 404; skipping Coverr."
                )
            elif e.response.status_code == 401:
                self.logger.warning("Coverr API key is missing or invalid; skipping Coverr")
            else:
                self.logger.warning(f"Error fetching from Coverr: {e}")
            return []
        except Exception as e:
            self.logger.warning(f"Error fetching from Coverr: {e}")
            return []
    
    async def search_youtube_cc(
        self,
        query: str,
        max_results: int = 20
    ) -> List[StockVideo]:
        """Search YouTube for Creative Commons licensed videos only.

        videoLicense=creativeCommon restricts results to clips explicitly
        marked reusable. Do not remove that filter — without it, results
        would be arbitrary copyrighted videos (ToS / copyright risk).
        """
        from src.core import config

        api_key = config.settings.youtube_developer_key
        if not api_key:
            self.logger.warning("YouTube developer key not configured")
            return []

        cache_key = f"{query.strip().lower()}::{max_results}"
        cached = self._youtube_cc_cache.get(cache_key)
        if cached is not None:
            return cached

        now = time.monotonic()
        if now < self._youtube_cc_cooldown_until:
            remaining = int(self._youtube_cc_cooldown_until - now)
            self.logger.info(f"YouTube CC search in cooldown for {remaining}s; skipping query: {query}")
            return []

        max_results = min(max_results, 3)

        try:
            async with httpx.AsyncClient() as client:
                search_resp = await client.get(
                    "https://www.googleapis.com/youtube/v3/search",
                    params={
                        "key": api_key,
                        "q": query,
                        "part": "snippet",
                        "type": "video",
                        "videoLicense": "creativeCommon",
                        "maxResults": max_results,
                        "safeSearch": "strict",
                    }
                )
                search_resp.raise_for_status()
                items = search_resp.json().get("items", [])

                video_ids = [item["id"]["videoId"] for item in items if item.get("id", {}).get("videoId")]
                if not video_ids:
                    self.logger.info(f"Found 0 Creative Commons videos on YouTube for: {query}")
                    self._youtube_cc_cache[cache_key] = []
                    return []

                details_resp = await client.get(
                    "https://www.googleapis.com/youtube/v3/videos",
                    params={
                        "key": api_key,
                        "id": ",".join(video_ids),
                        "part": "contentDetails",
                    }
                )
                details_resp.raise_for_status()
                durations = {
                    v["id"]: _parse_iso8601_duration(v.get("contentDetails", {}).get("duration", ""))
                    for v in details_resp.json().get("items", [])
                }

            results = []
            for item in items:
                video_id = item.get("id", {}).get("videoId")
                if not video_id:
                    continue
                snippet = item.get("snippet", {})
                thumbnails = snippet.get("thumbnails", {})
                stock_video = StockVideo(
                    id=video_id,
                    title=snippet.get("title", f"{query} - YouTube CC"),
                    url=f"https://www.youtube.com/watch?v={video_id}",
                    preview_url=(thumbnails.get("high") or thumbnails.get("default") or {}).get("url"),
                    download_url=f"https://www.youtube.com/watch?v={video_id}",
                    duration=durations.get(video_id, 0.0),
                    width=1920,
                    height=1080,
                    source="youtube_cc",
                    tags=[query]
                )
                results.append(stock_video)

            self.logger.info(f"Found {len(results)} Creative Commons videos on YouTube for: {query}")
            self._youtube_cc_cache[cache_key] = results
            return results

        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            if status == 429:
                self._youtube_cc_cooldown_until = time.monotonic() + 15 * 60
                self.logger.warning(
                    f"YouTube CC rate limited/quota limited (429); cooling down for 15 minutes. Query: {query}"
                )
            else:
                self.logger.warning(f"YouTube CC request failed with HTTP {status}. Query: {query}")
            self._youtube_cc_cache[cache_key] = []
            return []
        except Exception as e:
            self.logger.warning(f"Error fetching from YouTube CC for query '{query}': {type(e).__name__}: {e}")
            return []

    async def search_nasa(
        self,
        query: str,
        max_results: int = 10
    ) -> List[StockVideo]:
        """Search NASA's Image and Video Library. No API key needed — public domain
        (US government work), zero licensing risk. Best fit for space/earth/science
        visuals, not general b-roll."""
        try:
            async with httpx.AsyncClient() as client:
                search_resp = await client.get(
                    "https://images-api.nasa.gov/search",
                    params={"q": query, "media_type": "video"},
                )
                search_resp.raise_for_status()
                items = search_resp.json().get("collection", {}).get("items", [])[:max_results]

                results = []
                for item in items:
                    manifest_url = item.get("href")
                    if not manifest_url:
                        continue
                    data = (item.get("data") or [{}])[0]
                    links = item.get("links") or []
                    try:
                        manifest_resp = await client.get(manifest_url)
                        manifest_resp.raise_for_status()
                        assets = manifest_resp.json()
                    except Exception:
                        continue
                    download_url = next((u for u in assets if u.endswith("~medium.mp4")), None) \
                        or next((u for u in assets if u.endswith(".mp4")), None)
                    if not download_url:
                        continue
                    preview = next((l.get("href") for l in links if l.get("rel") == "preview"), None)
                    results.append(StockVideo(
                        id=data.get("nasa_id", download_url),
                        title=data.get("title", query),
                        url=f"https://images.nasa.gov/details/{data.get('nasa_id', '')}",
                        preview_url=preview,
                        download_url=download_url,
                        duration=0.0,  # not exposed by this API; extract_segment probes the real file
                        width=1920,
                        height=1080,
                        source="nasa",
                        tags=[query, *(data.get("keywords") or [])[:10]],
                    ))
                self.logger.info(f"Found {len(results)} videos on NASA Image/Video Library for: {query}")
                return results
        except Exception as e:
            self.logger.warning(f"Error fetching from NASA for query '{query}': {e}")
            return []

    async def search_vimeo(
        self,
        query: str,
        max_results: int = 10
    ) -> List[StockVideo]:
        """Search Vimeo for Creative Commons licensed videos (filter=CC).
        Uses VIMEO_ACCESS_TOKEN (personal access token, "public" scope)."""
        from src.core import config

        access_token = config.settings.vimeo_access_token
        if not access_token:
            self.logger.debug("Vimeo access token not configured; skipping")
            return []

        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    "https://api.vimeo.com/videos",
                    headers={"Authorization": f"Bearer {access_token}"},
                    params={
                        "query": query,
                        "filter": "CC",
                        "per_page": min(max_results, 25),
                        "sort": "relevant",
                    },
                )
                response.raise_for_status()
                payload = response.json()
                items = payload.get("data", payload if isinstance(payload, list) else [])

                results = []
                for video in items[:max_results]:
                    progressive = ((video.get("play") or {}).get("progressive")) or []
                    if not progressive:
                        continue
                    # Prefer the highest resolution that's still <=1080p.
                    by_height = sorted(progressive, key=lambda f: f.get("height") or 0)
                    best = next((f for f in reversed(by_height) if (f.get("height") or 0) <= 1080), by_height[-1])
                    download_url = best.get("link")
                    if not download_url:
                        continue
                    raw_tags = video.get("tags") or []
                    tag_names = [t.get("name", "") if isinstance(t, dict) else str(t) for t in raw_tags][:10]
                    pictures = ((video.get("pictures") or {}).get("sizes")) or []
                    results.append(StockVideo(
                        id=str(video.get("uri", download_url)).rsplit("/", 1)[-1],
                        title=video.get("name") or query,
                        url=video.get("link", ""),
                        preview_url=pictures[-1].get("link") if pictures else None,
                        download_url=download_url,
                        duration=float(video.get("duration") or 0),
                        width=int(best.get("width") or 1920),
                        height=int(best.get("height") or 1080),
                        source="vimeo",
                        tags=[query, *tag_names],
                    ))
                self.logger.info(f"Found {len(results)} videos on Vimeo for: {query}")
                return results
        except Exception as e:
            self.logger.warning(f"Error fetching from Vimeo for query '{query}': {e}")
            return []

    async def search_wikimedia(
        self,
        query: str,
        max_results: int = 10
    ) -> List[StockVideo]:
        """Search Wikimedia Commons for video files (public domain / various CC
        licenses). No API key needed. Quality/resolution is uneven (community
        uploads) — skip anything below 640px wide; the semantic relevance filter
        downstream handles the rest, same as for YouTube CC results."""
        # Wikimedia's API etiquette policy blocks requests without a descriptive
        # User-Agent (generic/default ones get 403'd).
        headers = {"User-Agent": "VideoAgent/1.0 (b-roll fetcher; https://github.com/video-agent)"}
        try:
            async with httpx.AsyncClient(headers=headers) as client:
                search_resp = await client.get(
                    "https://commons.wikimedia.org/w/api.php",
                    params={
                        "action": "query",
                        "list": "search",
                        "srsearch": f"{query} filetype:video",
                        "srnamespace": 6,
                        "srlimit": max_results,
                        "format": "json",
                    },
                )
                search_resp.raise_for_status()
                hits = search_resp.json().get("query", {}).get("search", [])
                if not hits:
                    return []

                titles = "|".join(h["title"] for h in hits)
                info_resp = await client.get(
                    "https://commons.wikimedia.org/w/api.php",
                    params={
                        "action": "query",
                        "titles": titles,
                        "prop": "imageinfo",
                        "iiprop": "url|size|mime",
                        "format": "json",
                    },
                )
                info_resp.raise_for_status()
                pages = info_resp.json().get("query", {}).get("pages", {})

                results = []
                for page in pages.values():
                    imageinfo = (page.get("imageinfo") or [{}])[0]
                    download_url = imageinfo.get("url")
                    mime = imageinfo.get("mime", "")
                    width = int(imageinfo.get("width") or 0)
                    if not download_url or not mime.startswith("video/") or width < 640:
                        continue
                    results.append(StockVideo(
                        id=str(page.get("pageid", download_url)),
                        title=page.get("title", query).removeprefix("File:"),
                        url=f"https://commons.wikimedia.org/wiki/{page.get('title', '')}",
                        preview_url=None,
                        download_url=download_url,
                        duration=float(imageinfo.get("duration") or 0),
                        width=width,
                        height=int(imageinfo.get("height") or 1080),
                        source="wikimedia",
                        tags=[query],
                    ))
                self.logger.info(f"Found {len(results)} videos on Wikimedia Commons for: {query}")
                return results
        except Exception as e:
            self.logger.warning(f"Error fetching from Wikimedia Commons for query '{query}': {e}")
            return []

    async def filter_by_relevance(
        self,
        videos: List[StockVideo],
        topic_context: str,
        concurrency: int = 5,
    ) -> List[StockVideo]:
        """Filter stock videos by semantic relevance using a cheap vision model.
        
        Sends each video's preview thumbnail to a vision model and asks whether
        the image content matches the topic. Rejects mismatches (e.g. Vietnam
        footage when topic is Africa).
        """
        from helpers.llm_task import call_openrouter_vision, TASK_MODELS

        api_key = os.getenv("OPENROUTER_API_KEY")
        if not api_key:
            self.logger.warning("OPENROUTER_API_KEY not set; skipping semantic filter")
            return videos

        model = TASK_MODELS["verify_stock_relevance"]

        prompt = (
            "You are a strict visual relevance checker for stock video selection.\n"
            f"The video topic/concept is: \"{topic_context}\"\n\n"
            "Look at this thumbnail image. Does it visually match or plausibly relate "
            "to the topic above? Consider geography, culture, setting, objects, and people.\n\n"
            "REJECT if:\n"
            "- Wrong country/continent (e.g. Asian city when topic is African geography)\n"
            "- Completely unrelated subject matter\n"
            "- Culturally mismatched (wrong architecture, wrong ethnicity for region-specific topic)\n\n"
            "ACCEPT if:\n"
            "- Visually matches topic's geography/subject\n"
            "- Generic enough to work (e.g. generic ocean for any coastal topic)\n"
            "- Partially relevant (some elements match)\n\n"
            "Respond with ONLY one word: ACCEPT or REJECT"
        )

        sem = asyncio.Semaphore(concurrency)
        
        async def check_one(video: StockVideo) -> tuple[StockVideo, bool]:
            if not video.preview_url:
                return (video, True)  # no thumbnail → keep by default
            async with sem:
                try:
                    result = await asyncio.to_thread(
                        call_openrouter_vision, model, prompt, video.preview_url, api_key
                    )
                    accepted = "ACCEPT" in result.upper()
                    if not accepted:
                        self.logger.info(
                            f"[semantic_filter] REJECTED: {video.title} "
                            f"(id={video.id}, source={video.source})"
                        )
                    return (video, accepted)
                except Exception as e:
                    self.logger.warning(f"[semantic_filter] error checking {video.id}: {e}")
                    return (video, True)  # on error → keep (fail-open)

        tasks = [check_one(v) for v in videos]
        results = await asyncio.gather(*tasks)
        
        accepted = [v for v, ok in results if ok]
        rejected_count = len(videos) - len(accepted)
        self.logger.info(
            f"[semantic_filter] {len(accepted)} accepted, {rejected_count} rejected "
            f"out of {len(videos)} candidates for topic: {topic_context[:80]}"
        )
        return accepted

    async def search_all_sources(
        self,
        query: str,
        max_results_per_source: int = 10,
        topic_context: Optional[str] = None
    ) -> List[StockVideo]:
        """Search all configured stock video sources.
        
        Args:
            query: search keywords
            max_results_per_source: max results per source
            topic_context: full topic/concept description for semantic filtering.
                           If provided, filters results via vision model.
        """
        import asyncio
        from src.core import config

        sources = {
            item.strip().lower()
            for item in (config.settings.stock_video_sources or "pexels,pixabay").split(",")
            if item.strip()
        }

        tasks = []
        if "pexels" in sources:
            tasks.append(self.search_pexels(query, max_results_per_source))
        if "pixabay" in sources:
            tasks.append(self.search_pixabay(query, max_results_per_source))
        if "coverr" in sources:
            tasks.append(self.search_coverr(query, max_results_per_source))
        if "youtube_cc" in sources or "youtube" in sources:
            tasks.append(self.search_youtube_cc(query, max_results_per_source))
        if "nasa" in sources:
            tasks.append(self.search_nasa(query, max_results_per_source))
        if "vimeo" in sources:
            tasks.append(self.search_vimeo(query, max_results_per_source))
        if "wikimedia" in sources:
            tasks.append(self.search_wikimedia(query, max_results_per_source))

        results = []
        if not tasks:
            self.logger.warning("No stock video sources enabled")
            return []

        for task_results in await asyncio.gather(*tasks, return_exceptions=True):
            if isinstance(task_results, list):
                results.extend(task_results)
        
        # Remove duplicates based on URL
        seen_urls = set()
        unique_results = []
        for video in results:
            if video.download_url not in seen_urls:
                seen_urls.add(video.download_url)
                unique_results.append(video)
        
        candidates = unique_results[:max_results_per_source * max(1, len(sources))]

        # Semantic relevance filter via vision model. Include the concrete search
        # query too; matching the broad topic is not enough if this clip will be
        # used for a specific narration beat.
        if topic_context and candidates:
            relevance_context = (
                f"Video topic/concept: {topic_context}\n"
                f"Requested stock visual/search query: {query}"
            )
            candidates = await self.filter_by_relevance(candidates, relevance_context)

        # Keep one provider from starving the others. Search results arrive grouped by
        # provider task order, so callers that take only the first few clips would
        # otherwise mostly see Pexels/Pixabay even when YouTube CC has usable matches.
        by_source: dict[str, list[StockVideo]] = {}
        for video in candidates:
            by_source.setdefault(video.source, []).append(video)

        balanced: list[StockVideo] = []
        source_order = [
            source for source in
            ("pexels", "pixabay", "coverr", "youtube_cc", "nasa", "vimeo", "wikimedia")
            if source in by_source
        ]
        source_order.extend(source for source in by_source if source not in source_order)
        while any(by_source.values()):
            for source in source_order:
                if by_source.get(source):
                    balanced.append(by_source[source].pop(0))

        return balanced

    # ─── Image Search Methods ─────────────────────────────────────────────

    async def search_pexels_images(
        self,
        query: str,
        max_results: int = 15,
        orientation: str = "landscape",
    ) -> List[StockImage]:
        """Search Pexels for stock images."""
        from src.core import config

        api_key = config.settings.pexels_api_key
        if not api_key:
            self.logger.warning("Pexels API key not configured")
            return []

        try:
            headers = {"Authorization": api_key}
            params = {
                "query": query,
                "per_page": max_results,
                "orientation": orientation,
            }
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    "https://api.pexels.com/v1/search",
                    headers=headers,
                    params=params,
                )
                resp.raise_for_status()
                data = resp.json()

            results = []
            for photo in data.get("photos", []):
                src = photo.get("src", {})
                results.append(StockImage(
                    id=str(photo["id"]),
                    title=photo.get("alt") or f"{query} - Pexels",
                    url=photo.get("url", ""),
                    preview_url=src.get("medium", ""),
                    download_url=src.get("original", src.get("large2x", "")),
                    width=photo.get("width", 1920),
                    height=photo.get("height", 1080),
                    source="pexels",
                    tags=[query],
                ))
            self.logger.info(f"Found {len(results)} images on Pexels for: {query}")
            return results
        except Exception as e:
            self.logger.warning(f"Error fetching images from Pexels: {e}")
            return []

    async def search_pixabay_images(
        self,
        query: str,
        max_results: int = 15,
        orientation: str = "horizontal",
    ) -> List[StockImage]:
        """Search Pixabay for stock images."""
        from src.core import config

        api_key = config.settings.pixabay_api_key
        if not api_key:
            self.logger.warning("Pixabay API key not configured")
            return []

        try:
            params = {
                "key": api_key,
                "q": query,
                "per_page": max(3, max_results),
                "image_type": "photo",
                "orientation": orientation,
                "order": "popular",
            }
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    "https://pixabay.com/api/",
                    params=params,
                )
                resp.raise_for_status()
                data = resp.json()

            results = []
            for hit in data.get("hits", []):
                results.append(StockImage(
                    id=str(hit["id"]),
                    title=hit.get("tags", query),
                    url=hit.get("pageURL", ""),
                    preview_url=hit.get("webformatURL", ""),
                    download_url=hit.get("largeImageURL", hit.get("webformatURL", "")),
                    width=hit.get("imageWidth", 1920),
                    height=hit.get("imageHeight", 1080),
                    source="pixabay",
                    tags=[query] + (hit.get("tags", "").split(", ")[:5]),
                ))
            self.logger.info(f"Found {len(results)} images on Pixabay for: {query}")
            return results
        except Exception as e:
            self.logger.warning(f"Error fetching images from Pixabay: {e}")
            return []

    async def search_unsplash_images(
        self,
        query: str,
        max_results: int = 15,
        orientation: str = "landscape",
    ) -> List[StockImage]:
        """Search Unsplash for stock images."""
        from src.core import config

        api_key = getattr(config.settings, "unsplash_api_key", "") or os.getenv("UNSPLASH_API_KEY", "")
        if not api_key:
            self.logger.debug("Unsplash API key not configured; skipping")
            return []

        try:
            headers = {"Authorization": f"Client-ID {api_key}"}
            params = {
                "query": query,
                "per_page": max_results,
                "orientation": orientation,
            }
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    "https://api.unsplash.com/search/photos",
                    headers=headers,
                    params=params,
                )
                resp.raise_for_status()
                data = resp.json()

            results = []
            for photo in data.get("results", []):
                urls = photo.get("urls", {})
                results.append(StockImage(
                    id=photo["id"],
                    title=photo.get("alt_description") or photo.get("description") or f"{query} - Unsplash",
                    url=photo.get("links", {}).get("html", ""),
                    preview_url=urls.get("small", ""),
                    download_url=urls.get("full", urls.get("regular", "")),
                    width=photo.get("width", 1920),
                    height=photo.get("height", 1080),
                    source="unsplash",
                    tags=[query] + [t["title"] for t in photo.get("tags", [])[:5] if "title" in t],
                ))
            self.logger.info(f"Found {len(results)} images on Unsplash for: {query}")
            return results
        except Exception as e:
            self.logger.warning(f"Error fetching images from Unsplash: {e}")
            return []

    async def filter_images_by_relevance(
        self,
        images: List[StockImage],
        topic_context: str,
        concurrency: int = 5,
    ) -> List[StockImage]:
        """Filter stock images by semantic relevance using vision model."""
        from helpers.llm_task import call_openrouter_vision, TASK_MODELS

        api_key = os.getenv("OPENROUTER_API_KEY")
        if not api_key:
            self.logger.warning("OPENROUTER_API_KEY not set; skipping image semantic filter")
            return images

        model = TASK_MODELS["verify_stock_relevance"]

        prompt = (
            "You are a strict visual relevance checker for stock image selection.\n"
            f"The video topic/concept is: \"{topic_context}\"\n\n"
            "Look at this image. Does it visually match or plausibly relate "
            "to the topic above? Consider geography, culture, setting, objects, and people.\n\n"
            "REJECT if:\n"
            "- Wrong country/continent (e.g. Asian city when topic is African geography)\n"
            "- Completely unrelated subject matter\n"
            "- Culturally mismatched (wrong architecture, wrong ethnicity for region-specific topic)\n\n"
            "ACCEPT if:\n"
            "- Visually matches topic's geography/subject\n"
            "- Generic enough to work (e.g. generic ocean for any coastal topic)\n"
            "- Partially relevant (some elements match)\n\n"
            "Respond with ONLY one word: ACCEPT or REJECT"
        )

        sem = asyncio.Semaphore(concurrency)

        async def check_one(img: StockImage) -> tuple[StockImage, bool]:
            preview = img.preview_url or img.download_url
            if not preview:
                return (img, True)
            async with sem:
                try:
                    result = await asyncio.to_thread(
                        call_openrouter_vision, model, prompt, preview, api_key
                    )
                    accepted = "ACCEPT" in result.upper()
                    if not accepted:
                        self.logger.info(
                            f"[semantic_filter:img] REJECTED: {img.title} "
                            f"(id={img.id}, source={img.source})"
                        )
                    return (img, accepted)
                except Exception as e:
                    self.logger.warning(f"[semantic_filter:img] error {img.id}: {e}")
                    return (img, True)

        tasks = [check_one(i) for i in images]
        results = await asyncio.gather(*tasks)

        accepted = [i for i, ok in results if ok]
        rejected_count = len(images) - len(accepted)
        self.logger.info(
            f"[semantic_filter:img] {len(accepted)} accepted, {rejected_count} rejected "
            f"out of {len(images)} image candidates"
        )
        return accepted

    async def search_all_images(
        self,
        query: str,
        max_results_per_source: int = 10,
        topic_context: Optional[str] = None,
        orientation: str = "landscape",
    ) -> List[StockImage]:
        """Search all configured image sources (Pexels, Pixabay, Unsplash).
        
        Args:
            query: search keywords
            max_results_per_source: max per source
            topic_context: if provided, runs semantic filter
            orientation: landscape / portrait / squarish
        """
        tasks = [
            self.search_pexels_images(query, max_results_per_source, orientation),
            self.search_pixabay_images(
                query, max_results_per_source,
                "horizontal" if orientation == "landscape" else "vertical"
            ),
            self.search_unsplash_images(query, max_results_per_source, orientation),
        ]

        results = []
        for task_results in await asyncio.gather(*tasks, return_exceptions=True):
            if isinstance(task_results, list):
                results.extend(task_results)

        # Deduplicate by download URL
        seen = set()
        unique = []
        for img in results:
            if img.download_url not in seen:
                seen.add(img.download_url)
                unique.append(img)

        candidates = unique[:max_results_per_source * 3]

        if topic_context and candidates:
            candidates = await self.filter_images_by_relevance(candidates, topic_context)

        return candidates
