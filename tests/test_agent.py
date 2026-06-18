"""
Example test cases for Video Agent
"""

import pytest
import asyncio
from unittest.mock import Mock, AsyncMock, patch

from src.core import LLMConfig, OpenRouterLLM, LLMMessage
from src.modules import VideoAgent, ContentSearcher, StockVideoFetcher


class TestLLM:
    """Test LLM integration"""
    
    @pytest.mark.asyncio
    async def test_llm_chat(self):
        """Test LLM chat functionality"""
        config = LLMConfig(api_key="test_key")
        llm = OpenRouterLLM(config)
        
        # Mock the API call
        with patch('httpx.AsyncClient.post') as mock_post:
            mock_response = AsyncMock()
            mock_response.json.return_value = {
                "choices": [{"message": {"content": "Test response"}}],
                "model": "test-model",
                "usage": {"total_tokens": 100}
            }
            mock_post.return_value = mock_response
            
            messages = [LLMMessage(role="user", content="Test message")]
            response = await llm.chat(messages)
            
            assert response.content == "Test response"
            assert response.model == "test-model"
        
        await llm.close()


class TestContentSearch:
    """Test content search functionality"""
    
    @pytest.mark.asyncio
    async def test_search_trending(self):
        """Test YouTube trending search"""
        searcher = ContentSearcher()
        
        # Mock YouTube API
        with patch('googleapiclient.discovery.build') as mock_build:
            mock_service = AsyncMock()
            mock_build.return_value = mock_service
            
            # Note: In real tests, mock the API response properly
            # This is a placeholder example


class TestVideoFetcher:
    """Test stock video fetching"""
    
    @pytest.mark.asyncio
    async def test_search_pexels(self):
        """Test Pexels search"""
        fetcher = StockVideoFetcher()
        
        with patch('httpx.AsyncClient.get') as mock_get:
            mock_response = AsyncMock()
            mock_response.json.return_value = {
                "videos": []
            }
            mock_get.return_value = mock_response
            
            # Test when Pexels API key is not configured
            videos = await fetcher.search_pexels("test query")
            assert videos == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
