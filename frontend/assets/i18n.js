/**
 * ReadySetClass™ Internationalization (i18n) System
 * Lightweight translation library with DOM auto-apply and backend persistence.
 *
 * FIXED:
 * - Added syncFromBackend() to pull preferred_language from user profile on load
 * - Fixed API_URL race condition — sync is called explicitly after API_URL is set
 * - Expanded supportedLanguages to all 9 supported languages
 */

class I18n {
    constructor() {
        this.translations = {};
        this.currentLang = 'en';
        this.defaultLang = 'en';
        this.supportedLanguages = {
            'en': { name: 'English',    flag: '🇺🇸', dir: 'ltr' },
            'es': { name: 'Español',    flag: '🇪🇸', dir: 'ltr' },
            'fr': { name: 'Français',   flag: '🇫🇷', dir: 'ltr' },
            'de': { name: 'Deutsch',    flag: '🇩🇪', dir: 'ltr' },
            'pt': { name: 'Português',  flag: '🇧🇷', dir: 'ltr' },
            'zh': { name: '中文',        flag: '🇨🇳', dir: 'ltr' },
            'ja': { name: '日本語',      flag: '🇯🇵', dir: 'ltr' },
            'ko': { name: '한국어',      flag: '🇰🇷', dir: 'ltr' },
            'hi': { name: 'हिन्दी',       flag: '🇮🇳', dir: 'ltr' },
            'ar': { name: 'العربية',    flag: '🇸🇦', dir: 'rtl' },
        };
    }

    /**
     * Initialize — priority: backend preference → localStorage → browser language → default.
     *
     * NOTE: syncFromBackend() must be called explicitly by the page script AFTER
     * window.API_URL is set. init() handles the offline/pre-auth path only.
     */
    async init() {
        const savedLang = localStorage.getItem('language');
        const browserLang = navigator.language.split('-')[0];
        const lang = savedLang ||
                     (this.supportedLanguages[browserLang] ? browserLang : this.defaultLang);

        // Pre-load default language so fallbacks work
        if (lang !== this.defaultLang) {
            try {
                const defResp = await fetch(`/locales/${this.defaultLang}.json`);
                if (defResp.ok) this.translations[this.defaultLang] = await defResp.json();
            } catch (e) { /* non-critical */ }
        }

        await this.loadLanguage(lang);
    }

    /**
     * Sync language preference from backend after login/page load.
     * Call this from your page script AFTER window.API_URL is set and the
     * user is authenticated (auth_token exists in localStorage).
     *
     * Usage:
     *   window.API_URL = 'https://api.readysetclass.app';
     *   await i18n.syncFromBackend();
     *
     * This will override localStorage if the backend has a different preference,
     * so the source of truth is always the user's saved profile.
     */
    async syncFromBackend() {
        const token = localStorage.getItem('auth_token');
        const apiBase = window.API_URL || '';

        if (!token || !apiBase) return; // Not authenticated or API not ready

        try {
            const response = await fetch(`${apiBase}/api/v2/user/language`, {
                method: 'GET',
                headers: {
                    'Authorization': 'Bearer ' + token
                }
            });

            if (!response.ok) return; // Silently fail — don't break the page

            const data = await response.json();
            const backendLang = data.preferred_language;

            if (backendLang && this.supportedLanguages[backendLang] && backendLang !== this.currentLang) {
                // Pre-load default for fallback if needed
                if (backendLang !== this.defaultLang && !this.translations[this.defaultLang]) {
                    try {
                        const defResp = await fetch(`/locales/${this.defaultLang}.json`);
                        if (defResp.ok) this.translations[this.defaultLang] = await defResp.json();
                    } catch (e) { /* non-critical */ }
                }
                await this.loadLanguage(backendLang);
            }
        } catch (e) {
            // Network error — keep whatever language is already active
        }
    }

    /**
     * Load translation file for a language and apply to DOM.
     */
    async loadLanguage(lang) {
        if (!this.supportedLanguages[lang]) {
            console.warn(`Language ${lang} not supported, falling back to ${this.defaultLang}`);
            lang = this.defaultLang;
        }

        try {
            const response = await fetch(`/locales/${lang}.json`);
            if (!response.ok) throw new Error(`Failed to load ${lang}`);

            this.translations[lang] = await response.json();
            this.currentLang = lang;

            // Persist to localStorage
            localStorage.setItem('language', lang);

            // Set HTML lang + dir attributes
            document.documentElement.lang = lang;
            document.documentElement.dir = this.supportedLanguages[lang].dir;

            // Apply translations to DOM
            this.applyTranslations();

            // Fire event for any other UI listeners
            window.dispatchEvent(new CustomEvent('languageChanged', {
                detail: { lang, langInfo: this.supportedLanguages[lang] }
            }));

            return true;
        } catch (error) {
            console.error(`Error loading language ${lang}:`, error);
            if (lang !== this.defaultLang) {
                return this.loadLanguage(this.defaultLang);
            }
            return false;
        }
    }

    /**
     * Apply translations to all data-i18n elements in the DOM.
     * Supports:
     *   data-i18n="key"              → sets textContent
     *   data-i18n-placeholder="key"  → sets placeholder attribute
     *   data-i18n-title="key"        → sets title attribute
     *   data-i18n-aria="key"         → sets aria-label attribute
     *   data-i18n-html="key"         → sets innerHTML (use sparingly)
     */
    applyTranslations() {
        document.querySelectorAll('[data-i18n]').forEach(el => {
            const key = el.getAttribute('data-i18n');
            const translated = this.t(key);
            if (translated && translated !== key) el.textContent = translated;
        });

        document.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
            const key = el.getAttribute('data-i18n-placeholder');
            const translated = this.t(key);
            if (translated && translated !== key) el.placeholder = translated;
        });

        document.querySelectorAll('[data-i18n-title]').forEach(el => {
            const key = el.getAttribute('data-i18n-title');
            const translated = this.t(key);
            if (translated && translated !== key) el.title = translated;
        });

        document.querySelectorAll('[data-i18n-aria]').forEach(el => {
            const key = el.getAttribute('data-i18n-aria');
            const translated = this.t(key);
            if (translated && translated !== key) el.setAttribute('aria-label', translated);
        });

        document.querySelectorAll('[data-i18n-html]').forEach(el => {
            const key = el.getAttribute('data-i18n-html');
            const translated = this.t(key);
            if (translated && translated !== key) el.innerHTML = translated;
        });
    }

    /**
     * Get translated text by dot-path key (e.g. "nav.courses").
     * Falls back to default language, then returns key if not found.
     */
    t(keyPath, variables = {}) {
        const resolve = (obj, keys) =>
            keys.reduce((o, k) => (o && typeof o === 'object' && k in o ? o[k] : undefined), obj);

        const keys = keyPath.split('.');
        let value = resolve(this.translations[this.currentLang], keys);

        if (value === undefined && this.currentLang !== this.defaultLang) {
            value = resolve(this.translations[this.defaultLang], keys);
        }

        if (value === undefined) {
            console.warn(`Translation key not found: ${keyPath}`);
            return keyPath;
        }

        if (typeof value === 'string' && Object.keys(variables).length > 0) {
            return value.replace(/\{\{(\w+)\}\}/g, (_, name) =>
                variables[name] !== undefined ? variables[name] : `{{${name}}}`
            );
        }

        return value;
    }

    getLang()               { return this.currentLang; }
    getLangInfo()           { return this.supportedLanguages[this.currentLang]; }
    getSupportedLanguages() { return this.supportedLanguages; }

    /**
     * Switch language — updates DOM and persists to backend.
     * window.API_URL must be set by the page script before this is called.
     */
    async setLanguage(lang) {
        if (lang === this.currentLang) return true;
        const result = await this.loadLanguage(lang);

        if (result) {
            const token = localStorage.getItem('auth_token');
            const apiBase = window.API_URL || '';
            if (token && apiBase) {
                fetch(`${apiBase}/api/v2/user/language`, {
                    method: 'PATCH',
                    headers: {
                        'Content-Type': 'application/json',
                        'Authorization': 'Bearer ' + token
                    },
                    body: JSON.stringify({ preferred_language: lang })
                }).catch(() => {});
            }
        }

        return result;
    }
}

// Global instance
const i18n = new I18n();

// Auto-initialize with localStorage/browser preference (no API needed)
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => i18n.init());
} else {
    i18n.init();
}

window.i18n = i18n;