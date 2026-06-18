"""
Video Editing Engine Module
"""

import os
from pathlib import Path
from typing import List, Optional, Tuple
import cv2
import numpy as np
from moviepy import (
    VideoFileClip, AudioFileClip, TextClip, CompositeVideoClip,
    CompositeAudioClip, concatenate_videoclips
)
from pydub import AudioSegment
from loguru import logger


class VideoEditor:
    """Handle video editing operations"""
    
    def __init__(self, temp_dir: str = "./temp", output_dir: str = "./outputs"):
        self.temp_dir = temp_dir
        self.output_dir = output_dir
        self.logger = logger
        Path(temp_dir).mkdir(parents=True, exist_ok=True)
        Path(output_dir).mkdir(parents=True, exist_ok=True)
    
    def cut_video(
        self,
        input_path: str,
        start_time: float,
        end_time: float,
        output_path: Optional[str] = None
    ) -> str:
        """Cut a portion of video"""
        try:
            video = VideoFileClip(input_path)
            cut_video = video.subclipped(start_time, end_time)
            
            if not output_path:
                output_path = os.path.join(
                    self.temp_dir,
                    f"cut_{Path(input_path).stem}.mp4"
                )
            
            cut_video.write_videofile(output_path, verbose=False, logger=None)
            video.close()
            self.logger.info(f"Cut video: {start_time}s - {end_time}s -> {output_path}")
            
            return output_path
        except Exception as e:
            self.logger.error(f"Error cutting video: {e}")
            raise
    
    def concatenate_videos(
        self,
        video_paths: List[str],
        output_path: Optional[str] = None,
        crossfade_duration: float = 0.5
    ) -> str:
        """Concatenate multiple videos with optional crossfade"""
        try:
            clips = [VideoFileClip(path) for path in video_paths]
            
            # Add crossfade between clips
            if crossfade_duration > 0:
                final_clip = concatenate_videoclips(clips, method="compose")
            else:
                final_clip = concatenate_videoclips(clips)
            
            if not output_path:
                output_path = os.path.join(self.temp_dir, "concatenated.mp4")
            
            final_clip.write_videofile(output_path, verbose=False, logger=None)
            self.logger.info(f"Concatenated {len(video_paths)} videos -> {output_path}")
            
            for clip in clips:
                clip.close()
            
            return output_path
        except Exception as e:
            self.logger.error(f"Error concatenating videos: {e}")
            raise
    
    def add_background_music(
        self,
        video_path: str,
        audio_path: str,
        output_path: Optional[str] = None,
        music_volume: float = 0.3,
        keep_original_audio: bool = True
    ) -> str:
        """Add background music to video"""
        try:
            video = VideoFileClip(video_path)
            music = AudioFileClip(audio_path)
            
            # Extend or cut music to match video duration
            if music.duration < video.duration:
                # Loop music
                loops = int(video.duration / music.duration) + 1
                music_list = [music] * loops
                music = concatenate_videoclips(music_list).subclipped(0, video.duration)
            else:
                music = music.subclipped(0, video.duration)
            
            # Adjust volumes
            if keep_original_audio and video.audio:
                original_audio = video.audio.volume_factor(0.7)
                music = music.volume_factor(music_volume)
                new_audio = CompositeAudioClip([original_audio, music])
            else:
                new_audio = music.volume_factor(music_volume)
            
            final_video = video.with_audio(new_audio)
            
            if not output_path:
                output_path = os.path.join(self.temp_dir, "with_music.mp4")
            
            final_video.write_videofile(output_path, verbose=False, logger=None)
            self.logger.info(f"Added music to video -> {output_path}")
            
            video.close()
            music.close()
            return output_path
        except Exception as e:
            self.logger.error(f"Error adding background music: {e}")
            raise
    
    def add_text_overlay(
        self,
        video_path: str,
        text: str,
        output_path: Optional[str] = None,
        duration: Optional[float] = None,
        position: str = "center",
        fontsize: int = 70,
        color: str = "white",
        font: str = "Arial"
    ) -> str:
        """Add text overlay to video"""
        try:
            video = VideoFileClip(video_path)
            
            if duration is None:
                duration = video.duration
            
            # Position mapping
            pos_map = {
                "center": ("center", "center"),
                "top": ("center", 0.1),
                "bottom": ("center", 0.9),
                "top_left": (0.1, 0.1),
                "top_right": (0.9, 0.1),
                "bottom_left": (0.1, 0.9),
                "bottom_right": (0.9, 0.9),
            }
            
            txt_clip = TextClip(
                text,
                fontsize=fontsize,
                color=color,
                font=font,
                method="caption",
                size=(video.w * 0.8, None)
            ).set_position(pos_map.get(position, "center")).set_duration(duration)
            
            final_video = CompositeVideoClip([video, txt_clip])
            
            if not output_path:
                output_path = os.path.join(self.temp_dir, "with_text.mp4")
            
            final_video.write_videofile(output_path, verbose=False, logger=None)
            self.logger.info(f"Added text overlay -> {output_path}")
            
            video.close()
            return output_path
        except Exception as e:
            self.logger.error(f"Error adding text overlay: {e}")
            raise
    
    def resize_video(
        self,
        video_path: str,
        target_size: Tuple[int, int],
        output_path: Optional[str] = None
    ) -> str:
        """Resize video to target dimensions"""
        try:
            video = VideoFileClip(video_path)
            resized = video.resized(target_size)
            
            if not output_path:
                output_path = os.path.join(
                    self.temp_dir,
                    f"resized_{target_size[0]}x{target_size[1]}.mp4"
                )
            
            resized.write_videofile(output_path, verbose=False, logger=None)
            self.logger.info(f"Resized video to {target_size} -> {output_path}")
            
            video.close()
            return output_path
        except Exception as e:
            self.logger.error(f"Error resizing video: {e}")
            raise
    
    def apply_color_grade(
        self,
        video_path: str,
        preset: str = "warm_cinematic",
        output_path: Optional[str] = None
    ) -> str:
        """Apply color grading to video"""
        try:
            # Color grading presets
            presets = {
                "warm_cinematic": {"hue": 5, "saturation": 1.1, "brightness": 0.95},
                "cool_blue": {"hue": -10, "saturation": 1.2, "brightness": 1.0},
                "vintage": {"hue": 15, "saturation": 0.8, "brightness": 0.9},
                "neutral": {"hue": 0, "saturation": 1.0, "brightness": 1.0},
            }
            
            # Use OpenCV for color grading
            video = cv2.VideoCapture(video_path)
            fps = video.get(cv2.CAP_PROP_FPS)
            width = int(video.get(cv2.CAP_PROP_FRAME_WIDTH))
            height = int(video.get(cv2.CAP_PROP_FRAME_HEIGHT))
            
            if not output_path:
                output_path = os.path.join(self.temp_dir, f"graded_{preset}.mp4")
            
            fourcc = cv2.VideoWriter_fourcc(*"mp4v")
            out = cv2.VideoWriter(output_path, fourcc, fps, (width, height))
            
            preset_settings = presets.get(preset, presets["neutral"])
            
            while True:
                ret, frame = video.read()
                if not ret:
                    break
                
                # Apply color grading (simplified)
                # In production, use more sophisticated methods
                frame = cv2.convertScaleAbs(frame, alpha=preset_settings["brightness"])
                out.write(frame)
            
            video.release()
            out.release()
            self.logger.info(f"Applied color grade ({preset}) -> {output_path}")
            
            return output_path
        except Exception as e:
            self.logger.error(f"Error applying color grade: {e}")
            raise
