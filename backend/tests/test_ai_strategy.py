import httpx

from app.ai_strategy import load_ai_layout_hint
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
    assert calls[0][1]["json"]["model"] == "deepseek-v4"
