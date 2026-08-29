import httpx

from app.ai_strategy import (
    load_ai_layout_hint,
    load_ai_layout_hint_diagnostic,
    verify_ai_connection,
    verify_ai_connection_diagnostic,
)
from app.models import CargoSpec, ContainerSpec, PackRequest


def ai_request() -> PackRequest:
    return PackRequest(
        container=ContainerSpec(
            id="ai-test",
            name="AI 测试柜",
            inner_length_mm=6000,
            inner_width_mm=2352,
            inner_height_mm=2393,
            door_width_mm=2340,
            door_height_mm=2280,
            max_payload_g=20_000_000,
        ),
        cargo_items=[
            CargoSpec(
                id="cargo-a",
                sku="ZT1",
                name="ZT1",
                kind="pallet",
                length_mm=700,
                width_mm=600,
                height_mm=900,
                weight_g=100_000,
                quantity=6,
                allowed_orientations=["LWH", "WLH"],
                stackable=True,
                max_layers=2,
                max_top_load_g=500_000,
            ),
            CargoSpec(
                id="cargo-b",
                sku="ZT2",
                name="ZT2",
                kind="pallet",
                length_mm=900,
                width_mm=700,
                height_mm=1000,
                weight_g=200_000,
                quantity=4,
                allowed_orientations=["LWH", "WLH"],
                stackable=False,
                max_layers=1,
                max_top_load_g=0,
            ),
        ],
    )


def test_no_api_key_disables_ai_without_network_call(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    def fail_if_called(*args, **kwargs):
        raise AssertionError("AI API should not be called without a key")

    monkeypatch.setattr(httpx, "post", fail_if_called)

    assert load_ai_layout_hint(ai_request()) is None


def test_reports_read_timeout_from_ai_provider(monkeypatch):
    captured = {}

    def timeout_post(_url, **kwargs):
        captured.update(kwargs)
        raise httpx.ReadTimeout("provider timed out")

    monkeypatch.setattr(httpx, "post", timeout_post)

    result = load_ai_layout_hint_diagnostic(ai_request(), api_key="test-key")

    assert result.hint is None
    assert result.error == "timeout"
    assert captured["timeout"] == 8.0


def test_parses_openai_compatible_deepseek_hint(monkeypatch):
    request = ai_request()
    monkeypatch.setenv("DEEPSEEK_API_KEY", "server-key")
    calls = []

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "choices": [{
                    "message": {
                        "content": '{"sku_order":["cargo-b","cargo-a"],"orientations":{"cargo-a":"WLH"}}'
                    }
                }]
            }

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return FakeResponse()

    monkeypatch.setattr(httpx, "post", fake_post)

    hint = load_ai_layout_hint(request)

    assert hint is not None
    assert hint.sku_order == ("cargo-b", "cargo-a")
    assert hint.orientations == {"cargo-a": "WLH"}
    assert calls[0][1]["headers"]["Authorization"] == "Bearer server-key"
    assert calls[0][1]["json"]["model"] == "deepseek-v4-flash"
    assert calls[0][1]["json"]["max_tokens"] == 512
    assert calls[0][1]["json"]["thinking"] == {"type": "disabled"}


def test_normalizes_repeated_sku_order_and_orientation_arrays(monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "choices": [{
                    "message": {
                        "content": (
                            '{"sku_order":["cargo-b","cargo-b","cargo-a"],'
                            '"orientations":{"cargo-a":["WLH","LWH"],'
                            '"cargo-b":["invalid","LWH"]}}'
                        )
                    }
                }]
            }

    monkeypatch.setattr(httpx, "post", lambda *_args, **_kwargs: FakeResponse())

    hint = load_ai_layout_hint(ai_request(), api_key="test-key")

    assert hint is not None
    assert hint.sku_order == ("cargo-b", "cargo-a")
    assert hint.orientations == {"cargo-a": "WLH", "cargo-b": "LWH"}


def test_parses_direct_orientation_mapping_from_provider(monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "choices": [{
                    "message": {
                        "content": (
                            '{"cargo-b":["LWH","WLH"],'
                            '"cargo-a":["WLH","LWH"]}'
                        )
                    }
                }]
            }

    monkeypatch.setattr(httpx, "post", lambda *_args, **_kwargs: FakeResponse())

    hint = load_ai_layout_hint(ai_request(), api_key="test-key")

    assert hint is not None
    assert hint.sku_order == ("cargo-b", "cargo-a")
    assert hint.orientations == {"cargo-b": "LWH", "cargo-a": "WLH"}


def test_parses_legal_ai_row_groups(monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "choices": [{
                    "message": {
                        "content": (
                            '{"sku_order":["cargo-a","cargo-b"],'
                            '"orientations":{},'
                            '"row_groups":[["cargo-a","cargo-b"],["cargo-a"],'
                            '["unknown","cargo-b"],["cargo-a","cargo-a","cargo-b"]]}'
                        )
                    }
                }]
            }

    monkeypatch.setattr(httpx, "post", lambda *_args, **_kwargs: FakeResponse())

    hint = load_ai_layout_hint(ai_request(), api_key="test-key")

    assert hint is not None
    assert hint.row_groups == (("cargo-a", "cargo-b"), ("cargo-a",))


def test_parses_structured_and_profile_ai_hint(monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "choices": [{
                    "message": {
                        "content": [{
                            "type": "text",
                            "text": (
                                '{"sku_order":["cargo-b","cargo-a"],'
                                '"profiles":{"easy":{"zone_order":["cargo-a","cargo-b"],'
                                '"max_zones":2}}}'
                            ),
                        }],
                    }
                }]
            }

    monkeypatch.setattr(httpx, "post", lambda *_args, **_kwargs: FakeResponse())

    hint = load_ai_layout_hint(ai_request(), api_key="test-key")

    assert hint is not None
    assert hint.sku_order == ("cargo-b", "cargo-a")
    assert hint.profiles["easy"].zone_order == ("cargo-a", "cargo-b")
    assert hint.profiles["easy"].max_zones == 2


def test_retries_without_response_format_when_provider_rejects_it(monkeypatch):
    calls = []

    class FakeResponse:
        def __init__(self, status):
            self.status_code = status

        def raise_for_status(self):
            if self.status_code == 400:
                request = httpx.Request("POST", "https://api.deepseek.com/v1/chat/completions")
                response = httpx.Response(400, request=request)
                raise httpx.HTTPStatusError(
                    "unsupported response format",
                    request=request,
                    response=response,
                )

        def json(self):
            return {"choices": [{"message": {"content": '{"sku_order":["cargo-a","cargo-b"]}'}}]}

    def fake_post(_url, **kwargs):
        calls.append(kwargs["json"])
        return FakeResponse(400 if len(calls) == 1 else 200)

    monkeypatch.setattr(httpx, "post", fake_post)

    hint = load_ai_layout_hint(ai_request(), api_key="test-key")

    assert hint is not None
    assert len(calls) == 2
    assert "response_format" in calls[0]
    assert "response_format" not in calls[1]


def test_uses_deepseek_v4_flash_as_the_default_model(monkeypatch):
    calls = []

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": "OK"}}]}

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return FakeResponse()

    monkeypatch.setattr(httpx, "post", fake_post)

    assert verify_ai_connection("deepseek-key", "deepseek", None, None)
    assert calls[0][1]["json"]["model"] == "deepseek-v4-flash"


def test_connection_diagnostic_explains_provider_credit_error(monkeypatch):
    request = httpx.Request("POST", "https://api.deepseek.com/v1/chat/completions")
    response = httpx.Response(402, request=request)

    class FailedResponse:
        def raise_for_status(self):
            raise httpx.HTTPStatusError("payment required", request=request, response=response)

    monkeypatch.setattr(httpx, "post", lambda *_args, **_kwargs: FailedResponse())

    result = verify_ai_connection_diagnostic("deepseek-key", "deepseek", None, None)

    assert result.message is None
    assert result.error == "http_error"
    assert result.status_code == 402


def test_uses_qwen_compatible_url_for_qwen_configuration(monkeypatch):
    calls = []

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": "OK"}}]}

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return FakeResponse()

    monkeypatch.setattr(httpx, "post", fake_post)

    assert verify_ai_connection(
        api_key="qwen-key",
        provider="qwen",
        model="qwen3-max",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    ) == "连接成功，模型可用于策略建议"
    assert calls[0][0] == "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"


def test_rejects_non_official_provider_address_without_network_call(monkeypatch):
    def fail_if_called(*args, **kwargs):
        raise AssertionError("untrusted address must not be called")

    monkeypatch.setattr(httpx, "post", fail_if_called)

    assert verify_ai_connection(
        api_key="test-key",
        provider="zhipu",
        model="glm-5.3",
        base_url="https://example.com/v1",
    ) is None


def test_uses_glm_53_as_the_default_zhipu_model(monkeypatch):
    calls = []

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": "OK"}}]}

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return FakeResponse()

    monkeypatch.setattr(httpx, "post", fake_post)

    assert verify_ai_connection("zhipu-key", "zhipu", None, None)
    assert calls[0][1]["json"]["model"] == "glm-5.3"
