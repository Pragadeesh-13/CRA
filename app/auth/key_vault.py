from typing import Dict, Any, Optional
import secrets

from app.auth.crypto import encrypt_string, decrypt_string, mask_secret

HOSTED_PROVIDERS = {"openrouter", "openai", "anthropic", "groq"}


def requires_key(cfg: Dict[str, Any]) -> bool:
    provider = (cfg.get("provider") or "").lower()
    return provider in HOSTED_PROVIDERS


class KeyVault:
    def __init__(self, encryption_secret: Optional[str] = None):
        self.encryption_secret = encryption_secret or secrets.token_bytes(32)
        # Maps user_id -> record
        self._memory_vault: Dict[str, Dict[str, Any]] = {}
        # Guest fallback slot
        self._guest_slot: Dict[str, Any] = {}

    def _resolve_key(self, session_user: Optional[Dict[str, Any]]) -> str:
        if not session_user:
            return "__guest__"
        return str(session_user.get("id") or session_user.get("uid") or session_user.get("username") or "__guest__")

    async def get(self, session_user: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        slot_key = self._resolve_key(session_user)
        rec = self._memory_vault.get(slot_key)
        if not rec and slot_key == "__guest__":
            rec = self._guest_slot

        if not rec:
            return None

        api_key = decrypt_string(rec["sealedKey"], self.encryption_secret) if rec.get("sealedKey") else ""
        return {
            "provider": rec.get("provider", "openrouter"),
            "model": rec.get("model", ""),
            "apiKey": api_key,
            "maskedKey": mask_secret(api_key),
            "persisted": rec.get("persisted", False),
        }

    async def put(self, session_user: Optional[Dict[str, Any]], record: Dict[str, Any]) -> Dict[str, Any]:
        slot_key = self._resolve_key(session_user)
        provider = (record.get("provider") or "openrouter").strip().lower()
        model = record.get("model", "").strip()
        api_key = record.get("apiKey", "").strip()

        data = {
            "provider": provider,
            "model": model,
            "sealedKey": encrypt_string(api_key, self.encryption_secret) if api_key else None,
            "persisted": slot_key != "__guest__",
        }

        if slot_key == "__guest__":
            self._guest_slot = data
        else:
            self._memory_vault[slot_key] = data

        return {"persisted": data["persisted"], "error": None}

    async def delete(self, session_user: Optional[Dict[str, Any]]) -> bool:
        slot_key = self._resolve_key(session_user)
        if slot_key == "__guest__":
            self._guest_slot.clear()
            return True
        return self._memory_vault.pop(slot_key, None) is not None

    def dispose(self):
        self._memory_vault.clear()
        self._guest_slot.clear()
