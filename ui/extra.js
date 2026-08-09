// extra.js — fork-added UI behaviour, kept out of the upstream script.js so merges
// stay clean. Talks to script.js through the small window.TTSApp bridge and the
// 'tts:refresh-history' event. Home for future fork UI too.
(function () {
    'use strict';

    function app() { return window.TTSApp || {}; }
    function apiBase() { return app().apiBase || ''; }
    function notify(msg, type, ms) { (app().notify || function () { })(msg, type, ms); }

    function fmtBytes(n) {
        n = n || 0;
        if (n < 1024) return n + ' B';
        if (n < 1048576) return (n / 1024).toFixed(1) + ' KB';
        return (n / 1048576).toFixed(1) + ' MB';
    }

    // ---- Generation history panel ----
    async function loadHistory() {
        const list = document.getElementById('history-list');
        const empty = document.getElementById('history-empty');
        if (!list) return;
        try {
            const r = await fetch(`${apiBase()}/api/history`);
            if (!r.ok) return;
            const items = (await r.json()).items || [];
            list.innerHTML = '';
            if (empty) empty.classList.toggle('hidden', items.length > 0);
            items.forEach(it => {
                const row = document.createElement('div');
                row.className = 'history-row';
                const label = document.createElement('span');
                label.className = 'history-row__label';
                label.textContent = `${(it.created || '').replace('T', ' ').replace('Z', '')} — ${it.voice || 'voice'}`;
                const audio = document.createElement('audio');
                audio.controls = true; audio.preload = 'none';
                audio.src = `${apiBase()}/history/file?name=${encodeURIComponent(it.filename)}`;
                const del = document.createElement('button');
                del.className = 'btn secondary small'; del.textContent = 'Delete';
                del.addEventListener('click', async () => {
                    const fd = new FormData(); fd.append('name', it.filename);
                    const rr = await fetch(`${apiBase()}/history/delete`, { method: 'POST', body: fd });
                    if (rr.ok) loadHistory(); else notify('Delete failed', 'error');
                });
                row.append(label, audio, del);
                list.appendChild(row);
            });
            if (app().isAdmin && app().isAdmin()) loadHistoryAdmin();
        } catch (e) { /* non-fatal */ }
    }

    function groupRow(text, onClear) {
        const row = document.createElement('div'); row.className = 'history-row';
        const s = document.createElement('span'); s.className = 'history-row__label'; s.textContent = text;
        const c = document.createElement('button'); c.className = 'btn secondary small'; c.textContent = 'Clear';
        c.addEventListener('click', onClear);
        row.append(s, c);
        return row;
    }

    async function loadHistoryAdmin() {
        const total = document.getElementById('history-admin-total');
        const bu = document.getElementById('history-by-user');
        const bv = document.getElementById('history-by-voice');
        if (!bu || !bv) return;
        try {
            const r = await fetch(`${apiBase()}/api/history/admin`);
            if (!r.ok) return;
            const g = await r.json();
            if (total) total.textContent = fmtBytes(g.total_size);
            bu.innerHTML = ''; bv.innerHTML = '';
            (g.by_user || []).forEach(u => bu.appendChild(groupRow(
                `${u.user} — ${u.items.length} (${fmtBytes(u.size)})`,
                async () => {
                    const fd = new FormData(); fd.append('scope', 'user'); fd.append('key', u.user);
                    if ((await fetch(`${apiBase()}/history/clear`, { method: 'POST', body: fd })).ok) loadHistory();
                })));
            (g.by_voice || []).forEach(v => bv.appendChild(groupRow(
                `${v.voice || '—'} — ${v.count} (${fmtBytes(v.size)})`,
                async () => {
                    const fd = new FormData(); fd.append('scope', 'voice'); fd.append('key', v.voice);
                    if ((await fetch(`${apiBase()}/history/clear`, { method: 'POST', body: fd })).ok) loadHistory();
                })));
        } catch (e) { /* non-fatal */ }
    }

    // ---- Best-of-N take picker ----
    async function generateVariations(n) {
        const container = document.getElementById('best-of-n-results');
        if (!container) return;
        const base = app().getTTSFormData ? app().getTTSFormData() : null;
        if (!base || !base.text || !base.text.trim()) { notify('Enter text first.', 'warning'); return; }
        container.innerHTML = '<p class="form-hint">Generating variations…</p>';
        const rows = [];
        for (let i = 0; i < n; i++) {
            const seed = 1000 + i;  // distinct non-zero seeds → each take reproducible
            const body = { ...base, seed, save_to_history: false, stream: false };
            try {
                const resp = await fetch(`${apiBase()}/tts`, {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body),
                });
                if (!resp.ok) continue;
                rows.push({ seed, url: URL.createObjectURL(await resp.blob()), i });
            } catch (e) { /* skip */ }
        }
        container.innerHTML = '';
        if (!rows.length) { container.innerHTML = '<p class="form-hint">No variations produced.</p>'; return; }
        rows.forEach(({ seed, url, i }) => {
            const row = document.createElement('div'); row.className = 'history-row';
            const label = document.createElement('span'); label.className = 'history-row__label';
            label.textContent = `Take ${i + 1} (seed ${seed})`;
            const audio = document.createElement('audio'); audio.controls = true; audio.src = url;
            const keep = document.createElement('button'); keep.className = 'btn primary small'; keep.textContent = 'Keep';
            keep.addEventListener('click', async () => {
                const kept = { ...base, seed, save_to_history: true, stream: false };
                const rr = await fetch(`${apiBase()}/tts`, {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(kept),
                });
                if (rr.ok) {
                    const u = URL.createObjectURL(await rr.blob());
                    if (app().showResult) app().showResult(u, { outputUrl: u, filename: `take_${i + 1}.wav`, submittedVoiceMode: base.voice_mode });
                    notify(`Kept take ${i + 1}.`, 'success');
                    container.innerHTML = '';
                    loadHistory();
                } else {
                    notify('Could not keep that take.', 'error');
                }
            });
            row.append(label, audio, keep);
            container.appendChild(row);
        });
    }

    function init() {
        const refresh = document.getElementById('history-refresh');
        if (refresh) refresh.addEventListener('click', loadHistory);
        const bestBtn = document.getElementById('best-of-n-btn');
        if (bestBtn) bestBtn.addEventListener('click', () => {
            const n = Math.max(2, Math.min(6, parseInt(document.getElementById('best-of-n')?.value, 10) || 3));
            generateVariations(n);
        });
        // script.js fires this after initial load and after each saved generation.
        document.addEventListener('tts:refresh-history', loadHistory);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
