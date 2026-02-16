/* ReadySetClass Student Edition — Shared JS */
/* Built by Phife */

const API_URL = 'https://facultyflow-production.up.railway.app';

// ===== AUTH =====

const auth = {
    getToken() {
        return localStorage.getItem('student_auth_token');
    },
    setToken(token) {
        localStorage.setItem('student_auth_token', token);
    },
    getUser() {
        try {
            return JSON.parse(localStorage.getItem('student_user'));
        } catch {
            return null;
        }
    },
    setUser(user) {
        localStorage.setItem('student_user', JSON.stringify(user));
    },
    isLoggedIn() {
        return !!this.getToken();
    },
    logout() {
        localStorage.removeItem('student_auth_token');
        localStorage.removeItem('student_user');
        window.location.href = '/login';
    }
};

function requireAuth() {
    if (!auth.isLoggedIn()) {
        window.location.href = '/login';
        return false;
    }
    return true;
}

function requireNoAuth() {
    if (auth.isLoggedIn()) {
        window.location.href = '/dashboard';
        return false;
    }
    return true;
}

// ===== API CLIENT =====

const api = {
    async request(method, path, body) {
        const headers = { 'Content-Type': 'application/json' };
        const token = auth.getToken();
        if (token) {
            headers['Authorization'] = `Bearer ${token}`;
        }
        const opts = { method, headers };
        if (body && method !== 'GET') {
            opts.body = JSON.stringify(body);
        }
        const res = await fetch(`${API_URL}${path}`, opts);
        if (res.status === 401) {
            auth.logout();
            return;
        }
        if (!res.ok) {
            const err = await res.json().catch(() => ({ detail: 'Request failed' }));
            throw new Error(err.detail || `Error ${res.status}`);
        }
        if (res.status === 204) return null;
        return res.json();
    },
    get(path) { return this.request('GET', path); },
    post(path, body) { return this.request('POST', path, body); },
    put(path, body) { return this.request('PUT', path, body); },
    del(path) { return this.request('DELETE', path); }
};

// ===== DATE HELPERS =====

function formatDate(iso) {
    if (!iso) return '';
    const d = new Date(iso);
    return d.toLocaleDateString('en-US', {
        month: 'short', day: 'numeric', year: 'numeric'
    });
}

function formatDateTime(iso) {
    if (!iso) return '';
    const d = new Date(iso);
    return d.toLocaleDateString('en-US', {
        month: 'short', day: 'numeric', year: 'numeric',
        hour: 'numeric', minute: '2-digit'
    });
}

function timeAgo(iso) {
    if (!iso) return '';
    const diff = Date.now() - new Date(iso).getTime();
    const mins = Math.floor(diff / 60000);
    if (mins < 1) return 'just now';
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.floor(mins / 60);
    if (hrs < 24) return `${hrs}h ago`;
    const days = Math.floor(hrs / 24);
    if (days < 7) return `${days}d ago`;
    return formatDate(iso);
}

function daysUntil(iso) {
    if (!iso) return null;
    const diff = new Date(iso).getTime() - Date.now();
    return Math.ceil(diff / (1000 * 60 * 60 * 24));
}

function dueDateLabel(iso) {
    const d = daysUntil(iso);
    if (d === null) return '';
    if (d < 0) return 'Overdue';
    if (d === 0) return 'Due today';
    if (d === 1) return 'Due tomorrow';
    if (d <= 7) return `Due in ${d} days`;
    return `Due ${formatDate(iso)}`;
}

// ===== UI HELPERS =====

function showAlert(id, message, type) {
    const el = document.getElementById(id);
    if (!el) return;
    el.textContent = message;
    el.className = `alert alert-${type || 'error'} show`;
}

function hideAlert(id) {
    const el = document.getElementById(id);
    if (el) el.className = 'alert';
}

function setLoading(btnId, loading, text) {
    const btn = document.getElementById(btnId);
    if (!btn) return;
    btn.disabled = loading;
    if (text) btn.textContent = text;
}

function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

// ===== NAV =====

function renderNav(activePage) {
    const user = auth.getUser();
    const nav = document.getElementById('main-nav');
    if (!nav) return;
    nav.innerHTML = `
        <a href="/dashboard" class="nav-brand">
            <span>ReadySetClass</span>
        </a>
        <div class="nav-links">
            <a href="/dashboard" class="${activePage === 'dashboard' ? 'active' : ''}">Home</a>
            <a href="/courses" class="${activePage === 'courses' ? 'active' : ''}">Courses</a>
            <a href="/calendar" class="${activePage === 'calendar' ? 'active' : ''}">Calendar</a>
            <a href="/grades" class="${activePage === 'grades' ? 'active' : ''}">Grades</a>
            <a href="/profile" class="${activePage === 'profile' ? 'active' : ''}">Profile</a>
        </div>
    `;
}

// ===== PWA INSTALL PROMPT =====

let deferredInstallPrompt = null;

window.addEventListener('beforeinstallprompt', (e) => {
    e.preventDefault();
    deferredInstallPrompt = e;
    showInstallBanner();
});

function showInstallBanner() {
    if (document.getElementById('install-banner')) return;
    const banner = document.createElement('div');
    banner.id = 'install-banner';
    banner.style.cssText = 'position: fixed; bottom: 0; left: 0; right: 0; background: #1B3A52; color: white; padding: 12px 16px; display: flex; align-items: center; justify-content: space-between; z-index: 300; box-shadow: 0 -2px 8px rgba(0,0,0,0.15);';
    banner.innerHTML = `
        <div style="display: flex; align-items: center; gap: 10px;">
            <span style="font-size: 1.3rem;">📱</span>
            <div>
                <div style="font-weight: 600; font-size: 0.9rem;">Add to Home Screen</div>
                <div style="font-size: 0.75rem; opacity: 0.7;">Quick access to ReadySetClass</div>
            </div>
        </div>
        <div style="display: flex; gap: 8px;">
            <button onclick="dismissInstallBanner()" style="background: none; border: none; color: rgba(255,255,255,0.6); cursor: pointer; padding: 6px; font-size: 0.8rem;">Later</button>
            <button onclick="installPWA()" style="background: #B8945F; color: white; border: none; border-radius: 6px; padding: 8px 14px; font-weight: 600; cursor: pointer; font-size: 0.8rem;">Install</button>
        </div>
    `;
    document.body.appendChild(banner);
}

function installPWA() {
    if (!deferredInstallPrompt) return;
    deferredInstallPrompt.prompt();
    deferredInstallPrompt.userChoice.then((choice) => {
        deferredInstallPrompt = null;
        dismissInstallBanner();
    });
}

function dismissInstallBanner() {
    const banner = document.getElementById('install-banner');
    if (banner) banner.remove();
    localStorage.setItem('rsc_install_dismissed', Date.now().toString());
}

// Don't show if dismissed recently (7 days)
window.addEventListener('beforeinstallprompt', (e) => {
    const dismissed = localStorage.getItem('rsc_install_dismissed');
    if (dismissed && Date.now() - parseInt(dismissed) < 7 * 24 * 60 * 60 * 1000) {
        return;
    }
});

// Register service worker
if ('serviceWorker' in navigator && window.location.protocol === 'https:') {
    navigator.serviceWorker.register('/sw.js').catch(() => {});
}
