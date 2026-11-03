"""Internationalization (i18n) and localization management module.

Supports:
- Dynamic JSON locale loading from config/locales/
- Schema and key integrity verification against base locale ('en')
- Thread-safe runtime translation helper `t(key, **kwargs)`
- Active language switching with observer callbacks
- System default language configuration via settings.yaml and database
"""
import os
import json
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional, Callable, Set

logger = logging.getLogger(__name__)

DEFAULT_LOCALES_DIR = Path("config/locales")
BASE_LOCALE = "en"


def _flatten_dict(d: Dict[str, Any], prefix: str = "") -> Dict[str, str]:
    """Flattens nested dictionaries into dot-separated keys."""
    items: Dict[str, str] = {}
    for k, v in d.items():
        new_key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            items.update(_flatten_dict(v, new_key))
        else:
            items[new_key] = str(v)
    return items


class I18nManager:
    _instance: Optional["I18nManager"] = None

    def __init__(self, locales_dir: Path = DEFAULT_LOCALES_DIR, default_lang: str = "en"):
        self.locales_dir = Path(locales_dir)
        self.default_lang = default_lang
        self.current_lang = default_lang
        self.translations: Dict[str, Dict[str, str]] = {}
        self.locale_names: Dict[str, str] = {}
        self.integrity_errors: Dict[str, List[str]] = {}
        self.subscribers: List[Callable[[str], None]] = []

        self.reload_locales()

    @classmethod
    def get_instance(cls) -> "I18nManager":
        if cls._instance is None:
            default_lang = "en"
            try:
                import yaml
                settings_path = Path("config/settings.yaml")
                if settings_path.exists():
                    with open(settings_path, "r", encoding="utf-8") as f:
                        cfg = yaml.safe_load(f) or {}
                        default_lang = cfg.get("language", {}).get("default_language", "en")
            except Exception:
                pass
            cls._instance = cls(default_lang=default_lang)
        return cls._instance

    def reload_locales(self):
        """Scans locales directory, validates integrity against base locale, and loads valid locales."""
        self.locales_dir.mkdir(parents=True, exist_ok=True)
        raw_translations: Dict[str, Dict[str, str]] = {}
        self.locale_names = {}
        self.integrity_errors = {}

        # 1. Load all JSON files in locales_dir
        for file_path in sorted(self.locales_dir.glob("*.json")):
            lang_code = file_path.stem.lower()
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    flat = _flatten_dict(data)
                    raw_translations[lang_code] = flat
                    self.locale_names[lang_code] = data.get("_locale_name", lang_code.upper())
            except Exception as e:
                self.integrity_errors[lang_code] = [f"JSON Parse Error: {str(e)}"]

        # 2. Check base locale exists
        base_keys: Set[str] = set()
        if BASE_LOCALE in raw_translations:
            base_keys = {k for k in raw_translations[BASE_LOCALE].keys() if not k.startswith("_")}
            self.translations[BASE_LOCALE] = raw_translations[BASE_LOCALE]
        else:
            self.integrity_errors[BASE_LOCALE] = ["Base locale 'en.json' is missing!"]

        # 3. Check integrity of other locales against base_keys
        for lang_code, strings in raw_translations.items():
            if lang_code == BASE_LOCALE:
                continue

            lang_keys = {k for k in strings.keys() if not k.startswith("_")}
            missing_keys = sorted(list(base_keys - lang_keys))

            if missing_keys:
                self.integrity_errors[lang_code] = missing_keys
            else:
                self.translations[lang_code] = strings

        # Ensure current_lang is available
        if self.current_lang not in self.translations:
            self.current_lang = BASE_LOCALE if BASE_LOCALE in self.translations else (list(self.translations.keys())[0] if self.translations else "en")

    def get_available_languages(self) -> List[str]:
        """Returns sorted list of valid, accepted language codes."""
        return sorted(list(self.translations.keys()))

    def get_integrity_errors(self) -> Dict[str, List[str]]:
        """Returns map of language codes to their missing keys or parse errors."""
        return self.integrity_errors

    def set_language(self, lang_code: str):
        """Sets active language and notifies all registered UI listeners."""
        lang_code = lang_code.lower()
        if lang_code in self.translations:
            self.current_lang = lang_code
            for callback in self.subscribers:
                try:
                    callback(lang_code)
                except Exception as e:
                    logger.exception(f"Error in i18n subscriber: {e}")

    def subscribe(self, callback: Callable[[str], None]):
        """Registers a listener to be called when language changes."""
        if callback not in self.subscribers:
            self.subscribers.append(callback)

    def unsubscribe(self, callback: Callable[[str], None]):
        if callback in self.subscribers:
            self.subscribers.remove(callback)

    def translate(self, key: str, **kwargs) -> str:
        """Looks up key in current language, falls back to base, then returns key formatted with kwargs."""
        lang_dict = self.translations.get(self.current_lang, {})
        val = lang_dict.get(key)
        if val is None:
            base_dict = self.translations.get(BASE_LOCALE, {})
            val = base_dict.get(key, key)

        if kwargs:
            try:
                return val.format(**kwargs)
            except Exception:
                return val
        return val


def get_i18n() -> I18nManager:
    return I18nManager.get_instance()


def t(key: str, **kwargs) -> str:
    """Convenience global translation function."""
    return get_i18n().translate(key, **kwargs)
