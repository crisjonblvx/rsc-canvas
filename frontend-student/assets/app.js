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
            <a href="/grades" class="${activePage === 'grades' ? 'active' : ''}">Grades</a>
            <a href="/profile" class="${activePage === 'profile' ? 'active' : ''}">Profile</a>
        </div>
    `;
}
