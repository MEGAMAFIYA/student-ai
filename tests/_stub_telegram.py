"""Compatibility shim: testlar uchun root `_stub_telegram.py` ni eksport qiladi."""
from _stub_telegram import install_stubs

__all__ = ["install_stubs"]
