"""Local text-completion scoring; importing this module does not load ML weights."""

from __future__ import annotations

import math
from typing import Any, cast


class EOTModel:
    HF_MODEL_ID = "HuggingFaceTB/SmolLM2-360M-Instruct"
    DEFAULT_THRESHOLD = 0.03
    MAX_HISTORY = 10
    EOT_TOKEN = "<|im_end|>"

    def __init__(
        self,
        threshold: float = DEFAULT_THRESHOLD,
        *,
        model_id: str = HF_MODEL_ID,
        max_input_tokens: int = 2048,
    ) -> None:
        if not math.isfinite(threshold) or not 0 <= threshold <= 1:
            raise ValueError("EOT threshold must be between 0 and 1")
        if max_input_tokens < 1:
            raise ValueError("EOT input token limit must be positive")

        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        self.threshold = threshold
        device = (
            "cuda"
            if torch.cuda.is_available()
            else "mps"
            if torch.backends.mps.is_available()
            else "cpu"
        )
        self.tokenizer: Any = AutoTokenizer.from_pretrained(model_id, truncation_side="left")
        self.model: Any = cast(
            Any,
            AutoModelForCausalLM.from_pretrained(
                model_id, dtype=torch.float32 if device == "cpu" else torch.float16
            ),
        )
        self.model.to(device)
        self.model.eval()
        self.max_input_tokens = min(max_input_tokens, self.model.config.max_position_embeddings)
        self.eot_token_id = self.tokenizer.convert_tokens_to_ids(self.EOT_TOKEN)
        if self.eot_token_id is None or self.eot_token_id == self.tokenizer.unk_token_id:
            raise ValueError("The configured model does not support the ChatML EOT token")

    def _convert_messages_to_chatml(self, messages: list[dict[str, Any]]) -> str:
        if not messages:
            return ""
        text: str = self.tokenizer.apply_chat_template(
            messages, add_generation_prompt=False, tokenize=False
        )
        last_eot_index = text.rfind(self.EOT_TOKEN)
        if last_eot_index < 0:
            raise ValueError("The configured chat template has no EOT marker")
        return text[:last_eot_index]

    def get_next_token_logprobs(self, prompt_text: str) -> dict[str, float]:
        """Score the EOT ID directly, even when it is outside the top 20 tokens."""
        if not prompt_text:
            return {}
        import torch

        inputs = self.tokenizer(
            prompt_text,
            return_tensors="pt",
            add_special_tokens=False,
            truncation=True,
            max_length=self.max_input_tokens,
        ).to(self.model.device)
        if inputs["input_ids"].shape[-1] == 0:
            return {}
        with torch.inference_mode():
            # This SmolLM2 uses Llama: only materialize the final token's logits.
            outputs = self.model(**inputs, logits_to_keep=1, use_cache=False)
            logits = outputs.logits[0, -1, :].float()
            logprob = torch.nn.functional.log_softmax(logits, dim=-1)[self.eot_token_id]
            return {self.EOT_TOKEN: float(logprob.item())}

    def process_result(
        self,
        top_logprobs: dict[str, float],
        target_tokens: tuple[str, ...] = (EOT_TOKEN,),
    ) -> tuple[float, str]:
        candidates = [
            (math.exp(logprob), token.strip())
            for token, logprob in top_logprobs.items()
            if token.strip() in target_tokens
        ]
        return max(candidates, default=(0.0, ""))

    def predict_eot_prob(self, messages: list[dict[str, Any]]) -> float:
        """Return a completion score, not a calibrated speech-transcription confidence."""
        if not messages or messages[-1].get("role") != "user":
            return 0.0
        if not str(messages[-1].get("content", "")).strip():
            return 0.0
        selected = messages[-self.MAX_HISTORY :]
        if any(
            message.get("role") not in {"system", "user", "assistant"}
            or not isinstance(message.get("content"), str)
            for message in selected
        ):
            raise ValueError("EOT history must contain text-only chat messages")
        prompt = self._convert_messages_to_chatml(selected)
        probability, _ = self.process_result(self.get_next_token_logprobs(prompt))
        if not math.isfinite(probability) or not 0 <= probability <= 1:
            raise ValueError("The EOT model returned an invalid completion score")
        return probability
