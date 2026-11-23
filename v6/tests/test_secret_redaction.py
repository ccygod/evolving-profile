from v6.secret_redaction import MARK, redact, redact_text

# Synthetic look-alikes of the formats seen in real chats (not real keys).
SK = "sk-" + "AbCdEf123456GhIjKl7890MnOpQr"
SK_DOTTED = "sk-ws-H.ABCDEFG.hIjK.MEUCIDE8TZZg6n7RSabc"
UUID_KEY = "00a0000a-1111-222b-8ccc-0d0e0f0a0b0c"


def test_shape_rules():
    out, counts = redact(f"把 APIkey 换成 {SK}  url：https://api.example.com/v1")
    assert SK not in out and MARK in out and "https://api.example.com/v1" in out
    assert counts.get("sk") == 1


def test_dotted_sk_and_context_uuid():
    text = f"晨报的APIKEY也用{UUID_KEY}  https://api.example.com/v1 fallback到{SK_DOTTED}"
    out = redact_text(text)
    assert UUID_KEY not in out and SK_DOTTED not in out
    assert "https://api.example.com/v1" in out


def test_ordinary_text_untouched():
    for text in ("key point 是 retention_worker 的批处理", "session 28ea184d-a757-4e55-a89b-d758edee8ce9 已完成",
                 "token 预算 12000", "postgres://localhost:15432/db",
                 "token-saving-mode-20261108T1200", "key=aHR0cHM6Ly93d3cuZXhhbXBsZS5jb20vYWJj1234",
                 '"key":"file_v2_20261108_abcd1234-5678"', "key: personal-memory-core-v2-shadow-20260811"):
        assert redact_text(text) == text


def test_url_password_and_bearer():
    out = redact_text("postgresql://admin:hunter2pass@127.0.0.1:15432/x Authorization: Bearer abcdefghijklmnop1234")
    assert "hunter2pass" not in out and "abcdefghijklmnop1234" not in out


def test_idempotent():
    once = redact_text(f"key={UUID_KEY} {SK}")
    assert redact_text(once) == once


def test_prose_slug_and_already_redacted_url_are_stable():
    assert redact_text("see sk-learn-pipeline-guide for details") == "see sk-learn-pipeline-guide for details"
    assert redact("postgres://u:[REDACTED]@host/db") == ("postgres://u:[REDACTED]@host/db", {})
