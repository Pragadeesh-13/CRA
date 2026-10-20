import pytest
from app.auth.crypto import encrypt_string, decrypt_string, mask_secret, derive_key
from app.auth.key_vault import KeyVault, requires_key


def test_crypto_roundtrip():
    secret = "my-ultra-secure-key-32-chars-long!"
    original_text = "sk-openrouter-secret-token-12345"

    encrypted = encrypt_string(original_text, secret)
    assert "ciphertext" in encrypted
    assert "iv" in encrypted
    assert "authTag" in encrypted
    assert encrypted["algorithm"] == "aes-256-gcm"

    decrypted = decrypt_string(encrypted, secret)
    assert decrypted == original_text


def test_mask_secret():
    assert mask_secret("sk-1234567890abcdef", visible=4) == "********cdef"
    assert mask_secret("tiny", visible=4) == "****"
    assert mask_secret("short", visible=4) == "*hort"
    assert mask_secret("", visible=4) == ""


def test_requires_key():
    assert requires_key({"provider": "openrouter"}) is True
    assert requires_key({"provider": "openai"}) is True
    assert requires_key({"provider": "anthropic"}) is True
    assert requires_key({"provider": "groq"}) is True
    assert requires_key({"provider": "lmstudio"}) is False
    assert requires_key({"provider": "mock"}) is False
    assert requires_key({"provider": "custom"}) is False


@pytest.mark.asyncio
async def test_key_vault_guest_and_user_storage():
    vault = KeyVault()

    # Guest store and retrieve
    res = await vault.put(None, {"provider": "openrouter", "model": "anthropic/claude-3-opus", "apiKey": "test-key-123"})
    assert res["persisted"] is False

    guest_val = await vault.get(None)
    assert guest_val is not None
    assert guest_val["provider"] == "openrouter"
    assert guest_val["apiKey"] == "test-key-123"
    assert guest_val["maskedKey"] == "********-123"

    # User store and retrieve
    user = {"uid": "user-456", "username": "minister"}
    res_user = await vault.put(user, {"provider": "openai", "model": "gpt-4o", "apiKey": "sk-user-secret"})
    assert res_user["persisted"] is True

    user_val = await vault.get(user)
    assert user_val is not None
    assert user_val["provider"] == "openai"
    assert user_val["apiKey"] == "sk-user-secret"

    # Delete guest
    await vault.delete(None)
    assert await vault.get(None) is None
    # User key remains
    assert await vault.get(user) is not None
