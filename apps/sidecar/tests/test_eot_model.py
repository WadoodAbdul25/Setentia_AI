from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
import torch
from sentia_sidecar.eot_model import EOTModel
from transformers import BatchEncoding


class FakeTokenizer:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []
        self.options: dict[str, Any] = {}

    def apply_chat_template(self, messages: list[dict[str, Any]], **kwargs: Any) -> str:
        self.messages = messages
        return "".join(
            f"<|im_start|>{message['role']}\n{message['content']}<|im_end|>\n"
            for message in messages
        )

    def __call__(self, prompt: str, **kwargs: Any) -> BatchEncoding:
        self.options = kwargs
        length = min(len(prompt), kwargs["max_length"]) if prompt else 0
        return BatchEncoding({"input_ids": torch.ones((1, length), dtype=torch.long)})


class FakeModel:
    device = torch.device("cpu")

    def __call__(self, **kwargs: Any) -> SimpleNamespace:
        # EOT is ranked 21st but exceeds the detector's 0.03 threshold.
        probabilities = torch.tensor([0.04] * 20 + [0.031] + [0.169 / 100] * 100)
        assert kwargs["logits_to_keep"] == 1
        assert kwargs["use_cache"] is False
        return SimpleNamespace(logits=probabilities.log().reshape(1, 1, -1))


@pytest.fixture
def model() -> EOTModel:
    result = EOTModel.__new__(EOTModel)
    result.tokenizer = FakeTokenizer()
    result.model = FakeModel()
    result.max_input_tokens = 16
    result.eot_token_id = 20
    return result


def test_prediction_returns_float_and_does_not_drop_eot_outside_top_twenty(
    model: EOTModel, capsys: pytest.CaptureFixture[str]
) -> None:
    score = model.predict_eot_prob([{"role": "user", "content": "Explain the voice flow."}])
    assert score == pytest.approx(0.031)
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize(
    "messages",
    [[], [{"role": "user", "content": "  "}], [{"role": "assistant", "content": "An answer"}]],
)
def test_empty_or_non_user_input_does_not_run_inference(
    model: EOTModel, messages: list[dict[str, str]]
) -> None:
    assert model.predict_eot_prob(messages) == 0
    assert model.tokenizer.options == {}


def test_tokenization_has_a_real_left_truncation_limit(model: EOTModel) -> None:
    model.predict_eot_prob([{"role": "user", "content": "A" * 10000}])
    assert model.tokenizer.options["truncation"] is True
    assert model.tokenizer.options["max_length"] == 16


def test_history_is_bounded_and_only_last_eot_marker_is_removed(model: EOTModel) -> None:
    messages = [{"role": "user", "content": str(index)} for index in range(20)]
    model.predict_eot_prob(messages)
    assert model.tokenizer.messages == messages[-10:]
    text = model._convert_messages_to_chatml(messages[-2:])
    assert text.count("<|im_end|>") == 1
    assert text.endswith("19")
    assert len(messages) == 20


@pytest.mark.parametrize("threshold", [-1, 2, float("nan"), float("inf")])
def test_invalid_threshold_is_rejected_before_loading_weights(threshold: float) -> None:
    with pytest.raises(ValueError, match="threshold"):
        EOTModel(threshold=threshold)


def test_invalid_history_is_rejected(model: EOTModel) -> None:
    with pytest.raises(ValueError, match="text-only"):
        model.predict_eot_prob([{"role": "user", "content": ["not text"]}])


def test_real_transformer_forward_accepts_last_token_only_inference(model: EOTModel) -> None:
    from transformers import LlamaConfig, LlamaForCausalLM

    model.model = LlamaForCausalLM(
        LlamaConfig(
            vocab_size=32,
            hidden_size=16,
            intermediate_size=32,
            num_hidden_layers=1,
            num_attention_heads=2,
            num_key_value_heads=2,
            max_position_embeddings=128,
        )
    ).eval()
    result = model.predict_eot_prob([{"role": "user", "content": "A short question"}])
    assert 0 <= result <= 1


@pytest.mark.parametrize("device", ["cpu", "mps", "cuda"])
def test_model_uses_available_device_and_bounded_context(
    monkeypatch: pytest.MonkeyPatch, device: str
) -> None:
    from transformers import AutoModelForCausalLM, AutoTokenizer

    tokenizer = SimpleNamespace(unk_token_id=0, convert_tokens_to_ids=lambda token: 2)
    loaded = SimpleNamespace(
        config=SimpleNamespace(max_position_embeddings=8192),
        to=lambda selected: setattr(loaded, "selected_device", selected),
        eval=lambda: None,
    )
    monkeypatch.setattr(torch.cuda, "is_available", lambda: device == "cuda")
    monkeypatch.setattr(torch.backends.mps, "is_available", lambda: device == "mps")
    monkeypatch.setattr(AutoTokenizer, "from_pretrained", lambda *args, **kwargs: tokenizer)
    monkeypatch.setattr(AutoModelForCausalLM, "from_pretrained", lambda *args, **kwargs: loaded)
    detector = EOTModel(max_input_tokens=10000)
    assert loaded.selected_device == device
    assert detector.max_input_tokens == 8192
