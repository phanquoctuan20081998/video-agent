"""
Voice & Subtitle Generation Module
"""

import os
from pathlib import Path
from typing import Optional, List, Tuple
import json
from pydub import AudioSegment
from pydub.playback import play
from loguru import logger


class VoiceoverGenerator:
    """Generate voiceover using TTS providers"""
    
    def __init__(self, provider: str = "elevenlabs"):
        self.provider = provider
        self.logger = logger
    
    async def generate_voiceover(
        self,
        text: str,
        voice_id: Optional[str] = None,
        output_path: Optional[str] = None,
        language: str = "en"
    ) -> str:
        """Generate voiceover audio"""
        if self.provider == "elevenlabs":
            return await self._generate_elevenlabs(text, voice_id, output_path, language)
        elif self.provider == "edge_tts":
            return await self._generate_edge_tts(text, output_path, language)
        elif self.provider == "gtts":
            return await self._generate_gtts(text, output_path, language)
        else:
            self.logger.error(f"Unknown TTS provider: {self.provider}")
            raise ValueError(f"Unknown TTS provider: {self.provider}")
    
    async def _generate_elevenlabs(
        self,
        text: str,
        voice_id: Optional[str],
        output_path: Optional[str],
        language: str
    ) -> str:
        """Generate using ElevenLabs API"""
        from src.core import config
        
        api_key = config.settings.elevenlabs_api_key
        if not api_key:
            self.logger.error("ElevenLabs API key not configured")
            raise ValueError("ElevenLabs API key not configured")
        
        try:
            import httpx
            
            if not voice_id:
                voice_id = (
                    config.settings.elevenlabs_voice_id
                    or config.settings.voice_id
                    or "Rachel"
                )
            model_id = config.settings.elevenlabs_model_id or "eleven_multilingual_v2"
            language_code = (config.settings.elevenlabs_language_code or language or "").split("-")[0]
            
            headers = {
                "xi-api-key": api_key,
                "Content-Type": "application/json"
            }
            
            data = {
                "text": text,
                "model_id": model_id,
                "voice_settings": {
                    "stability": 0.5,
                    "similarity_boost": 0.75
                }
            }
            # language_code is only honored by Turbo v2.5 / Flash v2.5 — other models
            # (including the eleven_multilingual_v2 default) return 400 Bad Request
            # if it's present at all, even when set to a valid code.
            LANGUAGE_CODE_MODELS = {"eleven_turbo_v2_5", "eleven_flash_v2_5"}
            if language_code and model_id in LANGUAGE_CODE_MODELS:
                data["language_code"] = language_code
            
            if not output_path:
                output_path = f"./temp/voiceover_{hash(text)}.mp3"
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)

            async with httpx.AsyncClient(timeout=60) as client:
                # Check quota before submitting
                quota_resp = await client.get(
                    "https://api.elevenlabs.io/v1/user",
                    headers={"xi-api-key": api_key},
                )
                if quota_resp.status_code == 200:
                    sub = quota_resp.json().get("subscription", {})
                    remaining = sub.get("character_limit", 0) - sub.get("character_count", 0)
                    if remaining < len(text):
                        raise ValueError(
                            f"ElevenLabs quota too low: {remaining} chars left, need {len(text)}. "
                            "Reset on next billing cycle or upgrade plan."
                        )

                response = await client.post(
                    f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
                    headers=headers,
                    json=data
                )
                response.raise_for_status()
                audio_bytes = response.content

            with open(output_path, "wb") as f:
                f.write(audio_bytes)
            
            self.logger.info(f"Generated voiceover -> {output_path}")
            return output_path
            
        except Exception as e:
            self.logger.error(f"Error generating voiceover with ElevenLabs: {e}")
            raise
    
    async def _generate_edge_tts(
        self,
        text: str,
        output_path: Optional[str],
        language: str
    ) -> str:
        """Generate using Edge TTS (free)"""
        try:
            import edge_tts
            
            voice = "vi-VN-HoaiMyNeural" if language.startswith("vi") else "en-US-AriaNeural"
            
            if not output_path:
                output_path = f"./temp/voiceover_edge_{hash(text)}.mp3"
            
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            
            communicate = edge_tts.Communicate(text, voice)
            await communicate.save(output_path)
            
            self.logger.info(f"Generated voiceover (Edge TTS) -> {output_path}")
            return output_path
            
        except Exception as e:
            self.logger.error(f"Error generating voiceover with Edge TTS: {e}")
            raise

    async def _generate_gtts(
        self,
        text: str,
        output_path: Optional[str],
        language: str
    ) -> str:
        """Generate using Google TTS (free, no API key)."""
        try:
            from gtts import gTTS
            import asyncio

            if not output_path:
                output_path = f"./temp/voiceover_gtts_{hash(text)}.mp3"

            Path(output_path).parent.mkdir(parents=True, exist_ok=True)

            loop = asyncio.get_event_loop()
            tts_lang = "vi" if language.startswith("vi") else "en"
            tts = gTTS(text=text, lang=tts_lang, slow=False)
            await loop.run_in_executor(None, tts.save, output_path)

            self.logger.info(f"Generated voiceover (gTTS) -> {output_path}")
            return output_path

        except Exception as e:
            self.logger.error(f"Error generating voiceover with gTTS: {e}")
            raise


class SubtitleGenerator:
    """Generate subtitles for video"""
    
    def __init__(self, provider: str = "whisper"):
        self.provider = provider
        self.logger = logger
    
    async def generate_subtitles(
        self,
        audio_path: str,
        output_path: Optional[str] = None,
        language: str = "en"
    ) -> str:
        """Generate subtitles from audio"""
        if self.provider == "whisper":
            return await self._generate_whisper(audio_path, output_path, language)
        elif self.provider == "elevenlabs_scribe":
            return await self._generate_elevenlabs_scribe(audio_path, output_path)
        else:
            self.logger.error(f"Unknown subtitle provider: {self.provider}")
            raise ValueError(f"Unknown subtitle provider: {self.provider}")
    
    async def _generate_whisper(
        self,
        audio_path: str,
        output_path: Optional[str],
        language: str
    ) -> str:
        """Generate subtitles using Whisper"""
        try:
            from faster_whisper import WhisperModel
            
            # Load model (large-v3-turbo for faster processing)
            model = WhisperModel("large-v3-turbo", device="cpu", compute_type="float32")
            
            # Transcribe
            segments, info = model.transcribe(
                audio_path,
                language=language,
                word_level=True
            )
            
            # Convert to SRT format
            srt_content = self._segments_to_srt(segments)
            
            if not output_path:
                output_path = f"./temp/subtitles_{Path(audio_path).stem}.srt"
            
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            
            with open(output_path, "w") as f:
                f.write(srt_content)
            
            self.logger.info(f"Generated subtitles -> {output_path}")
            return output_path
            
        except Exception as e:
            self.logger.error(f"Error generating subtitles with Whisper: {e}")
            raise
    
    async def _generate_elevenlabs_scribe(
        self,
        audio_path: str,
        output_path: Optional[str]
    ) -> str:
        """Generate subtitles using ElevenLabs Scribe API"""
        from src.core import config
        
        api_key = config.settings.elevenlabs_api_key
        if not api_key:
            self.logger.error("ElevenLabs API key not configured")
            raise ValueError("ElevenLabs API key not configured")
        
        try:
            import httpx
            
            headers = {"xi-api-key": api_key}
            
            with open(audio_path, "rb") as f:
                files = {"audio": f}
                async with httpx.AsyncClient() as client:
                    response = await client.post(
                        "https://api.elevenlabs.io/v1/audio-to-text",
                        headers=headers,
                        files=files
                    )
                    response.raise_for_status()
            
            data = response.json()
            srt_content = self._segments_to_srt(data.get("segments", []))
            
            if not output_path:
                output_path = f"./temp/subtitles_{Path(audio_path).stem}.srt"
            
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            
            with open(output_path, "w") as f:
                f.write(srt_content)
            
            self.logger.info(f"Generated subtitles (ElevenLabs) -> {output_path}")
            return output_path
            
        except Exception as e:
            self.logger.error(f"Error generating subtitles with ElevenLabs: {e}")
            raise
    
    def _segments_to_srt(self, segments: List) -> str:
        """Convert segments to SRT format"""
        srt_lines = []
        for i, segment in enumerate(segments, 1):
            if isinstance(segment, dict):
                start = segment.get("start", 0)
                end = segment.get("end", 0)
                text = segment.get("text", "")
            else:
                # Handle Whisper segment objects
                start = segment.start
                end = segment.end
                text = segment.text
            
            srt_lines.append(str(i))
            srt_lines.append(self._format_time(start) + " --> " + self._format_time(end))
            srt_lines.append(text)
            srt_lines.append("")
        
        return "\n".join(srt_lines)
    
    @staticmethod
    def _format_time(seconds: float) -> str:
        """Format seconds to SRT time format"""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        millis = int((seconds % 1) * 1000)
        
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"
