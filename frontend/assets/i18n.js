/**
 * ReadySetClass™ Internationalization (i18n) System
 * Lightweight translation library with DOM auto-apply and backend persistence.
 */

class I18n {
    constructor() {
        this.translations = {};
        this.currentLang = 'en';
        this.defaultLang = 'en';
        this._stylesInjected = false;
        this.supportedLanguages = {
            'en': { name: 'English',    flag: '🇺🇸', dir: 'ltr' },
            'es': { name: 'Español',    flag: '🇪🇸', dir: 'ltr' },
            'fr': { name: 'Français',   flag: '🇫🇷', dir: 'ltr' },
            'pt': { name: 'Português',  flag: '🇧🇷', dir: 'ltr' },
            'ar': { name: 'العربية',    flag: '🇸🇦', dir: 'rtl' },
            'zh': { name: '中文',        flag: '🇨🇳', dir: 'ltr' },
        };
    }

    /**
     * Initialize — priority: localStorage → browser language → default.
     * Note: user profile preferred_language is synced later by dashboard
     * code once API_URL is available (i18n.js loads before main script).
     */
    async init() {
        this.ensureGlobalLanguageStyles();

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
     * Inject global language styles once:
     * - Arabic RTL layout fixes
     * - Arabic/Chinese font stacks
     */
    ensureGlobalLanguageStyles() {
        if (this._stylesInjected || document.getElementById('rsc-i18n-global-styles')) {
            this._stylesInjected = true;
            return;
        }

        const style = document.createElement('style');
        style.id = 'rsc-i18n-global-styles';
        style.textContent = `
            :lang(ar), [lang="ar"] {
                font-family: 'Noto Sans Arabic', 'Arial', 'Tahoma', sans-serif !important;
            }

            :lang(zh), :lang(zh-CN), [lang="zh"] {
                font-family: 'Noto Sans SC', 'PingFang SC', 'Microsoft YaHei', sans-serif !important;
            }

            [dir="rtl"] {
                text-align: right;
            }

            [dir="rtl"] .flex-row {
                flex-direction: row-reverse;
            }

            /* Canvas dashboard/sidebar specific RTL corrections */
            [dir="rtl"] .dashboard {
                direction: rtl;
            }

            [dir="rtl"] .sidebar {
                left: auto !important;
                right: 0 !important;
                border-right: none !important;
                border-left: 1px solid rgba(0, 0, 0, 0.08) !important;
            }

            [dir="rtl"] .main-content,
            [dir="rtl"] .header,
            [dir="rtl"] .content-wrapper {
                direction: rtl;
            }

            [dir="rtl"] .nav-item {
                border-right: none !important;
                border-left: 3px solid transparent !important;
            }

            [dir="rtl"] .nav-item.active {
                border-right: none !important;
                border-left-color: var(--purple, #1E3A5F) !important;
            }

            [dir="rtl"] .icon-directional {
                transform: scaleX(-1);
            }

            [dir="rtl"] .pl-4 { padding-left: 0 !important; padding-right: 1rem !important; }
            [dir="rtl"] .pr-4 { padding-right: 0 !important; padding-left: 1rem !important; }
            [dir="rtl"] .ml-2 { margin-left: 0 !important; margin-right: 0.5rem !important; }
            [dir="rtl"] .mr-2 { margin-right: 0 !important; margin-left: 0.5rem !important; }
        `;

        (document.head || document.documentElement).appendChild(style);
        this._stylesInjected = true;
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
        // Text content
        document.querySelectorAll('[data-i18n]').forEach(el => {
            const key = el.getAttribute('data-i18n');
            const translated = this.t(key);
            if (translated && translated !== key) {
                el.textContent = translated;
            }
        });

        // Input placeholders
        document.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
            const key = el.getAttribute('data-i18n-placeholder');
            const translated = this.t(key);
            if (translated && translated !== key) {
                el.placeholder = translated;
            }
        });

        // Title attributes (tooltips)
        document.querySelectorAll('[data-i18n-title]').forEach(el => {
            const key = el.getAttribute('data-i18n-title');
            const translated = this.t(key);
            if (translated && translated !== key) {
                el.title = translated;
            }
        });

        // Aria-labels
        document.querySelectorAll('[data-i18n-aria]').forEach(el => {
            const key = el.getAttribute('data-i18n-aria');
            const translated = this.t(key);
            if (translated && translated !== key) {
                el.setAttribute('aria-label', translated);
            }
        });

        // innerHTML (for strings with inline markup)
        document.querySelectorAll('[data-i18n-html]').forEach(el => {
            const key = el.getAttribute('data-i18n-html');
            const translated = this.t(key);
            if (translated && translated !== key) {
                el.innerHTML = translated;
            }
        });
    }

    /**
     * Get translated text by dot-path key (e.g. "nav.courses").
     * Falls back to default language, then returns key if not found.
     */
    t(keyPath, variables = {}) {
        const resolve = (obj, keys) => {
            return keys.reduce((o, k) => (o && typeof o === 'object' && k in o ? o[k] : undefined), obj);
        };

        const keys = keyPath.split('.');
        let value = resolve(this.translations[this.currentLang], keys);

        // Fallback to default language
        if (value === undefined && this.currentLang !== this.defaultLang) {
            value = resolve(this.translations[this.defaultLang], keys);
        }

        if (value === undefined) {
            console.warn(`Translation key not found: ${keyPath}`);
            return keyPath;
        }

        // Interpolate {{variable}} placeholders
        if (typeof value === 'string' && Object.keys(variables).length > 0) {
            return value.replace(/\{\{(\w+)\}\}/g, (_, name) =>
                variables[name] !== undefined ? variables[name] : `{{${name}}}`
            );
        }

        return value;
    }

    /** Get current language code */
    getLang() { return this.currentLang; }

    /** Get info about current language */
    getLangInfo() { return this.supportedLanguages[this.currentLang]; }

    /** Get list of all supported languages */
    getSupportedLanguages() { return this.supportedLanguages; }

    /**
     * Switch language — updates DOM and persists to backend.
     * Uses window.API_URL (set by dashboard/page scripts) for the API base.
     */
    async setLanguage(lang) {
        if (lang === this.currentLang) return true;
        const result = await this.loadLanguage(lang);

        // Persist to backend (fire-and-forget)
        if (result) {
            const token = localStorage.getItem('auth_token');
            const apiBase = window.API_URL || '';
            if (token && apiBase) {
                fetch(apiBase + '/api/v2/user/language', {
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

// Auto-initialize when DOM is ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => i18n.init());
} else {
    i18n.init();
}

// Make available globally
window.i18n = i18n;
