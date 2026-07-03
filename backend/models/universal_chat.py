"""
Universal chat model abstraction.
Provides a plug-and-play interface to OpenAI-compatible backends.
"""

from typing import List, Dict, Optional, AsyncIterator
from ..utils.httpx_compat import patch_httpx_for_openai

patch_httpx_for_openai()
from openai import OpenAI
import asyncio
import time


class UniversalChat:
    """
    Universal chat client that abstracts over different OpenAI-compatible backends.
    
    Supports:
    - Qwen's OpenAI-compatible API
    - Local Ollama
    - Local vLLM
    - Any other OpenAI-compatible server
    
    Future enhancements:
    - Token counting and cost estimation
    - Async streaming support
    - Conversation history persistence (Redis/DB)
    """
    
    def __init__(
        self,
        model_name: str,
        base_url: str,
        api_key: str,
        system_prompt: str,
        default_temperature: float = 0.7,
        default_max_tokens: int = 1024
    ):
        """
        Initialize the universal chat client.
        
        Args:
            model_name: Name of the model to use (e.g., "qwen-plus", "llama3-8b")
            base_url: Base URL of the OpenAI-compatible endpoint
            api_key: API key (can be dummy for local models)
            system_prompt: System message to set the assistant's behavior
            default_temperature: Default sampling temperature
            default_max_tokens: Default maximum tokens to generate
        """
        self.model_name = model_name
        self.base_url = base_url
        self.api_key = api_key
        self.system_prompt = system_prompt
        self.default_temperature = default_temperature
        self.default_max_tokens = default_max_tokens
        
        # Initialize OpenAI client
        self.client = OpenAI(
            api_key=api_key,
            base_url=base_url
        )
        
        # In-memory conversation history per user
        # Future: Replace with Redis or database
        self._conversation_history: Dict[str, List[Dict[str, str]]] = {}
    
    def _get_messages(self, user_id: str, new_message: str, use_history: bool = True) -> List[Dict[str, str]]:
        """
        Build the messages list for the API call.
        
        Args:
            user_id: User identifier
            new_message: The new user message
        
        Returns:
            List of messages in OpenAI chat format
        """
        messages = [{"role": "system", "content": self.system_prompt}]
        
        # Add conversation history
        if use_history and user_id in self._conversation_history:
            messages.extend(self._conversation_history[user_id])
        
        # Add new user message
        messages.append({"role": "user", "content": new_message})
        
        return messages
    
    def _update_history(self, user_id: str, user_message: str, assistant_message: str) -> None:
        """
        Update the conversation history for a user.
        
        Args:
            user_id: User identifier
            user_message: The user's message
            assistant_message: The assistant's response
        """
        if user_id not in self._conversation_history:
            self._conversation_history[user_id] = []
        
        self._conversation_history[user_id].append(
            {"role": "user", "content": user_message}
        )
        self._conversation_history[user_id].append(
            {"role": "assistant", "content": assistant_message}
        )
    
    def chat(
        self,
        user_id: str,
        message: str,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        use_history: bool = True,
    ) -> str:
        """
        Send a message and get a response.
        
        Args:
            user_id: User identifier for conversation tracking
            message: The user's message
            temperature: Sampling temperature (uses default if not provided)
            max_tokens: Maximum tokens to generate (uses default if not provided)
        
        Returns:
            The assistant's response as plain text
        
        Raises:
            Exception: If the API call fails
        """
        # Use defaults if not provided
        temperature = temperature if temperature is not None else self.default_temperature
        max_tokens = max_tokens if max_tokens is not None else self.default_max_tokens
        
        # Build messages
        messages = self._get_messages(user_id, message, use_history=use_history)
        
        # Call the API
        try:
            response = self.client.chat.completions.create(
                model=self.model_name,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens
            )
            
            assistant_message = response.choices[0].message.content
            
            # Update conversation history when enabled
            if use_history:
                self._update_history(user_id, message, assistant_message)
            
            return assistant_message
        
        except Exception as e:
            raise Exception(f"Model API call failed: {str(e)}")
    
    async def astream(
        self,
        user_id: str,
        message: str,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        use_history: bool = True,
    ) -> AsyncIterator[str]:
        """Yield token chunks from an OpenAI-compatible streaming response."""
        temperature = temperature if temperature is not None else self.default_temperature
        max_tokens = max_tokens if max_tokens is not None else self.default_max_tokens
        messages = self._get_messages(user_id, message, use_history=use_history)
        collected: List[str] = []

        try:
            stream = self.client.chat.completions.create(
                model=self.model_name,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=True,
            )

            for chunk in stream:
                delta = chunk.choices[0].delta
                content = getattr(delta, "content", None) or ""
                if not content:
                    continue
                collected.append(content)
                yield content
                await asyncio.sleep(0)

            if use_history and collected:
                self._update_history(user_id, message, "".join(collected))
        except Exception as e:
            raise Exception(f"Model streaming API call failed: {str(e)}")
    
    def clear_history(self, user_id: str) -> None:
        """
        Clear conversation history for a user.
        
        Args:
            user_id: User identifier
        """
        if user_id in self._conversation_history:
            del self._conversation_history[user_id]
    
    def get_history(self, user_id: str) -> List[Dict[str, str]]:
        """
        Get conversation history for a user.
        
        Args:
            user_id: User identifier
        
        Returns:
            List of messages in the conversation history
        """
        return self._conversation_history.get(user_id, []).copy()
    
    def estimate_tokens(self, text: str) -> int:
        """
        Estimate token count for text.
        
        Future enhancement: Implement proper tokenization based on model type.
        For now, use a simple approximation.
        
        Args:
            text: Text to estimate tokens for
        
        Returns:
            Estimated token count
        """
        # Simple approximation: ~4 characters per token
        return len(text) // 4
    
    def estimate_cost(self, input_tokens: int, output_tokens: int) -> float:
        """
        Estimate cost for a request.
        
        Future enhancement: Implement cost calculation based on model pricing.
        
        Args:
            input_tokens: Number of input tokens
            output_tokens: Number of output tokens
        
        Returns:
            Estimated cost in USD
        """
        # Placeholder for future implementation
        return 0.0
