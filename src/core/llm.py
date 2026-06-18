"""
OpenRouter LLM Integration — supports task-based model routing.

Claude Code orchestrates; OpenRouter handles bulk tasks with optimal cheap models.
"""

import httpx
import json
import subprocess
import sys
from typing import Optional, AsyncGenerator
from pydantic import BaseModel, Field
from loguru import logger


class LLMConfig(BaseModel):
    """LLM Configuration"""
    api_key: str
    base_url: str = "https://openrouter.ai/api/v1"
    model: str = "meta-llama/llama-3.3-70b-instruct"
    temperature: float = 0.7
    max_tokens: int = 4000
    timeout: int = 300


class LLMMessage(BaseModel):
    """LLM Message format"""
    role: str  # "user", "assistant", "system"
    content: str


class LLMResponse(BaseModel):
    """LLM Response format"""
    content: str
    model: str
    tokens_used: int = 0


# Task → model mapping. Override per-call with model= param.
TASK_MODEL_MAP: dict[str, str] = {
    "generate_script":  "meta-llama/llama-3.3-70b-instruct",   # creative, long-form
    "generate_edl":     "deepseek/deepseek-r1",                  # structured JSON reasoning
    "analyze_content":  "google/gemini-flash-1.5",               # fast, cheap analysis
    "generate_seo":     "meta-llama/llama-3.1-8b-instruct",      # short structured output
    "generate_concept": "meta-llama/llama-3.3-70b-instruct",     # creative planning
    "default":          "meta-llama/llama-3.3-70b-instruct",
}


def model_for_task(task: str) -> str:
    return TASK_MODEL_MAP.get(task, TASK_MODEL_MAP["default"])


class OpenRouterLLM:
    """OpenRouter LLM client — streaming + non-streaming, task-aware model routing."""

    def __init__(self, config: LLMConfig):
        self.config = config
        self.async_client: Optional[httpx.AsyncClient] = None
        logger.info(f"OpenRouter LLM initialized, default model: {config.model}")

    async def chat(
        self,
        messages: list[LLMMessage],
        stream: bool = False,
        task: Optional[str] = None,
        model: Optional[str] = None,
    ) -> Optional[LLMResponse | AsyncGenerator]:
        """
        Send chat request to OpenRouter.

        Args:
            messages: Conversation messages
            stream: Stream response chunks
            task: Task name for automatic model selection (e.g. 'generate_script')
            model: Explicit model override (highest priority)
        """
        resolved_model = model or (model_for_task(task) if task else self.config.model)

        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "HTTP-Referer": "https://github.com/video-agent",
            "X-Title": "Video Agent",
            "Content-Type": "application/json",
        }
        payload = {
            "model": resolved_model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
            "stream": stream,
        }

        logger.debug(f"LLM call: task={task} model={resolved_model}")

        try:
            if stream:
                return self._stream_response(headers, payload)
            else:
                async with httpx.AsyncClient(timeout=self.config.timeout) as client:
                    response = await client.post(
                        f"{self.config.base_url}/chat/completions",
                        headers=headers,
                        json=payload,
                    )
                    response.raise_for_status()
                    data = response.json()
                    return LLMResponse(
                        content=data["choices"][0]["message"]["content"],
                        model=data["model"],
                        tokens_used=data.get("usage", {}).get("total_tokens", 0),
                    )
        except httpx.HTTPError as e:
            logger.error(f"LLM API error: {e}")
            raise

    async def _stream_response(self, headers: dict, payload: dict) -> AsyncGenerator:
        async with httpx.AsyncClient(timeout=self.config.timeout) as client:
            async with client.stream(
                "POST",
                f"{self.config.base_url}/chat/completions",
                headers=headers,
                json=payload,
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        data_str = line[6:]
                        if data_str and data_str != "[DONE]":
                            try:
                                data = json.loads(data_str)
                                if data.get("choices"):
                                    delta = data["choices"][0].get("delta", {})
                                    if "content" in delta:
                                        yield delta["content"]
                            except json.JSONDecodeError:
                                continue

    def run_task(self, task: str, input_text: str, extra_args: list[str] = None) -> str:
        """
        Delegate a task to helpers/llm_task.py (subprocess).
        Returns raw output string.
        Use for fire-and-forget calls from sync context.
        """
        cmd = [
            sys.executable, "helpers/llm_task.py",
            "--task", task,
            "--input", input_text,
        ] + (extra_args or [])
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"llm_task failed: {result.stderr}")
        return result.stdout.strip()

    async def close(self):
        if self.async_client:
            await self.async_client.aclose()
            self.async_client = None
