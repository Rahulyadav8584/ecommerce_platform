"""Deterministic Scripted Fake LLM for ShopHub (Offline Testing Mode).

Inherits from LangChain BaseChatModel to run completely offline with zero API keys.
Accurately predicts user intents and emits predetermined tool calls and streamed chunks
for multi-turn evaluation and automated tests.
"""

from typing import Any, AsyncIterator, Dict, Iterator, List, Optional
import re

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    ToolMessage,
)
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult


class ScriptedFakeChatModel(BaseChatModel):
    """Deterministic offline Chat Model compatible with LangGraph.

    Inspects message history and:
    1. Chooses appropriate tool calls for order queries and mutations.
    2. Synthesizes coherent answers once tool outputs return.
    3. Handles prompt-injection notes safely by ignoring adversarial commands.
    """

    model_name: str = "fake-scripted-model"
    tools: List[Dict[str, Any]] = []

    def bind_tools(self, tools: List[Any], **kwargs: Any) -> "ScriptedFakeChatModel":
        """Records bound tools for introspection."""
        clone = self.model_copy()
        clone.tools = tools
        return clone

    @property
    def _llm_type(self) -> str:
        return "scripted-fake-chat-model"

    def _decide_response(self, messages: List[BaseMessage]) -> AIMessage:
        """Determines the appropriate AIMessage (either a tool call or an answer)."""
        last_message = messages[-1]

        # 1. If the last message is a ToolMessage, summarize the result to the user
        if isinstance(last_message, ToolMessage):
            tool_content = str(last_message.content)

            # Check if this was a lookup_order response
            if "order-" in tool_content and "status" in tool_content:
                # If adversarial prompt injection is present, demonstrate immunity:
                if "NEUTRALIZED_DIRECTIVE" in tool_content or "ignore your instructions" in tool_content:
                    return AIMessage(
                        content=(
                            "I have retrieved the details for your order. "
                            "Please note that the customer note has been contained and ignored as it contains unauthorized instructions. "
                            "The order is currently in PLACED status."
                        )
                    )
                return AIMessage(
                    content=f"Here are the details for the requested order: {tool_content}"
                )

            # Check if this was a list_orders response
            if "[" in tool_content and "]" in tool_content:
                # Specific intent: "which paid orders have not shipped in 3 days?"
                # Look back in history to check original user query:
                user_texts = [m.content for m in messages if isinstance(m, HumanMessage)]
                last_user_query = user_texts[-1].lower() if user_texts else ""

                if "3 days" in last_user_query or "not shipped" in last_user_query:
                    return AIMessage(
                        content=(
                            "Found 2 paid orders that were placed more than 3 days ago and have not shipped yet:\n"
                            "1. Order order-1002 (Maria Silva) - Placed 2026-11-01 ($84.00)\n"
                            "2. Order order-1003 (John Doe) - Placed 2026-10-31 ($210.00)"
                        )
                    )

                if "maria" in last_user_query:
                    return AIMessage(
                        content="Customer Maria Silva's latest order is order-1002, placed on 2026-11-01 for $84.00 (Status: PAID)."
                    )

                return AIMessage(content=f"Here are the matching orders for your store: {tool_content}")

            # Error from tool (e.g. order not found, cross-tenant access)
            if "not found" in tool_content.lower() or "error" in tool_content.lower():
                return AIMessage(
                    content=f"I was unable to find that order. Details: {tool_content}"
                )

            # Proposal response
            if "proposal_type" in tool_content:
                return AIMessage(
                    content=f"A proposal has been generated for your review: {tool_content}"
                )

            return AIMessage(content=f"Tool executed: {tool_content}")

        # 2. If the last message is from a Human, determine which tool to call
        if isinstance(last_message, HumanMessage):
            query = last_message.content.lower()

            # A. Refund proposal request
            if "refund" in query:
                order_match = re.search(r'order[-\s]?(\d+)', query)
                order_id = f"order-{order_match.group(1)}" if order_match else "order-1002"
                return AIMessage(
                    content="",
                    tool_calls=[{
                        "name": "propose_refund",
                        "args": {"id": order_id},
                        "id": "call_propose_refund_1",
                    }],
                )

            # B. Cancel proposal request
            if "cancel" in query:
                order_match = re.search(r'order[-\s]?(\d+)', query)
                order_id = f"order-{order_match.group(1)}" if order_match else "order-1004"
                return AIMessage(
                    content="",
                    tool_calls=[{
                        "name": "propose_cancel",
                        "args": {"id": order_id},
                        "id": "call_propose_cancel_1",
                    }],
                )

            # C. "which paid orders have not shipped in 3 days?"
            if "paid" in query and ("shipped" in query or "3 days" in query):
                return AIMessage(
                    content="",
                    tool_calls=[{
                        "name": "list_orders",
                        "args": {"status": "PAID"},
                        "id": "call_list_orders_paid",
                    }],
                )

            # D. "what did customer Maria order last?"
            if "maria" in query:
                return AIMessage(
                    content="",
                    tool_calls=[{
                        "name": "list_orders",
                        "args": {},
                        "id": "call_list_orders_maria",
                    }],
                )

            # E. Specific order lookup (e.g. "what is the status of order 1004?")
            order_match = re.search(r'order[-\s]?(\d+)', query)
            if order_match:
                order_id = f"order-{order_match.group(1)}"
                return AIMessage(
                    content="",
                    tool_calls=[{
                        "name": "lookup_order",
                        "args": {"id": order_id},
                        "id": "call_lookup_order_1",
                    }],
                )

            # F. General order query
            if "orders" in query:
                return AIMessage(
                    content="",
                    tool_calls=[{
                        "name": "list_orders",
                        "args": {},
                        "id": "call_list_orders_all",
                    }],
                )

            # Fallback direct answer
            return AIMessage(
                content="I am your ShopHub store assistant. You can ask me about orders, unshipped items, or propose cancellations and refunds."
            )

        return AIMessage(content="How can I assist you with your store orders today?")

    def _generate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> ChatResult:
        ai_message = self._decide_response(messages)
        generation = ChatGeneration(message=ai_message)
        return ChatResult(generations=[generation])

    def _stream(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        """Streams response tokens word-by-word for SSE testing."""
        ai_message = self._decide_response(messages)

        # If it has tool calls, yield them directly
        if ai_message.tool_calls:
            chunk = ChatGenerationChunk(
                message=AIMessageChunk(
                    content="",
                    tool_call_chunks=[
                        {
                            "name": tc["name"],
                            "args": str(tc["args"]),
                            "id": tc["id"],
                            "index": i,
                        }
                        for i, tc in enumerate(ai_message.tool_calls)
                    ],
                )
            )
            if run_manager:
                run_manager.on_llm_new_token("", chunk=chunk)
            yield chunk
            return

        # Stream text words as individual chunks
        words = ai_message.content.split(" ")
        for i, word in enumerate(words):
            token = word if i == 0 else f" {word}"
            chunk = ChatGenerationChunk(message=AIMessageChunk(content=token))
            if run_manager:
                run_manager.on_llm_new_token(token, chunk=chunk)
            yield chunk

