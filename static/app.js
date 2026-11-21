'use strict';
// Dashboard. Every piece of data is inserted with textContent (never innerHTML),
// so an ad or customer name can't inject code into this page.
(() => {
  const CSRF = document.querySelector('meta[name=csrf]').content;
  const main = document.getElementById('main');
  const AG = {
    media_buyer: { name: 'Media buyer', letter: 'م' },
    followup: { name: 'متابعة العملاء', letter: 'ع' },
    weekly_review: { name: 'المراجعة الأسبوعية', letter: 'ر' },
    owner: { name: 'انت', letter: 'ص' },
    system: { name: 'الجدول', letter: 'ج' },
  };
  const MODE = { read: ['قراية بس', 'c-meas'], auto: ['أوتوماتيك', 'c-done'], ask: ['بموافقتك', 'c-wait'], no: ['ممنوع', 'c-no'], allowed: ['مسموح', 'c-done'], owner: ['انت بتبعت', 'c-wait'], '—': ['—', 'c-fail'] };
  const SEG = { confirm: ['تأكيد طلب', 'c-wait'], delivered: ['بعد الاستلام', 'c-done'], repeat: ['يشتري تاني', 'c-meas'], lapsed: ['بقاله فترة', 'c-lav'] };
  const PAY = { cod: 'كاش عند الاستلام', online: 'أونلاين' };
  let STATUS = null;
  let current = '';
  const ui = { teamFilter: 'all', teamAgent: 'media_buyer', teamDay: '', decTab: 'pending', custTab: 'followups', seg: 'all' };

  // ---------------------------------------------------------------- helpers
  function h(tag, attrs, ...kids) {
    const el = document.createElement(tag);
    if (attrs) {
      for (const [k, v] of Object.entries(attrs)) {
        if (v === null || v === undefined || v === false) continue;
        if (k === 'class') el.className = v;
        else if (k === 'style') Object.assign(el.style, v);
        else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v);
        else if (k === 'value') el.value = v;
        else if (k === 'checked') el.checked = !!v;
        else el.setAttribute(k, v === true ? '' : String(v));
      }
    }
    for (const kid of kids.flat(Infinity)) {
      if (kid === null || kid === undefined || kid === false) continue;
      el.append(kid instanceof Node ? kid : String(kid));
    }
    return el;
  }
  async function api(path, opts = {}) {
    const init = { method: opts.method || 'GET', headers: {} };
    if (opts.body !== undefined) { init.headers['Content-Type'] = 'application/json'; init.body = JSON.stringify(opts.body); }
    if (init.method !== 'GET') init.headers['X-CSRF-Token'] = CSRF;
    const r = await fetch(path, init);
    let data = null;
    try { data = await r.json(); } catch (e) { data = null; }
    if (!r.ok) throw new Error((data && (data.detail || data.error)) || ('HTTP ' + r.status));
    return data;
  }
  let toastTimer = null;
  function toast(msg) {
    const t = document.getElementById('toast');
    t.textContent = msg; t.hidden = false;
    clearTimeout(toastTimer); toastTimer = setTimeout(() => { t.hidden = true; }, 3500);
  }
  const fmt = (n, d = 0) => (n === null || n === undefined || isNaN(n)) ? '—' : Number(n).toLocaleString('en-US', { maximumFractionDigits: d });
  const cur = () => (STATUS && STATUS.account && STATUS.account.currency && STATUS.account.currency !== 'EGP') ? STATUS.account.currency : 'ج.م';
  // small amounts (cost per engagement or per view) need decimals, or 0.04 shows as 0
  const money = (n) => (n === null || n === undefined) ? '—' : fmt(n, Math.abs(n) > 0 && Math.abs(n) < 10 ? 2 : 0) + ' ' + cur();
  const hm = (iso) => (iso || '').slice(11, 16);
  const dayOf = (iso) => (iso || '').slice(0, 10);
  const todayStr = () => { const d = new Date(); return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0'); };
  const chip = (label, cls) => h('span', { class: 'chip ' + cls }, label);
  const av = (key) => h('div', { class: 'av av-' + (AG[key] ? key : 'system'), 'aria-hidden': 'true' }, (AG[key] || AG.system).letter);
  const clip = (s, n) => { s = String(s || ''); return s.length > n ? s.slice(0, n - 1) + '…' : s; };
  function busy(btn, fn) {
    return async (ev) => {
      if (ev) ev.preventDefault();
      btn.disabled = true;
      try { await fn(); } catch (e) { toast(e.message); } finally { btn.disabled = false; }
    };
  }
  function decisionChip(d) {
    if (d.verdict === 'right') return chip('الفرضية صح', 'c-done');
    if (d.verdict === 'wrong') return chip('الفرضية غلط', 'c-no');
    if (d.verdict === 'unclear') return chip('النتيجة مش واضحة', 'c-fail');
    const m = {
      pending: ['مستني موافقتك', 'c-wait'], approved: ['اتوافق', 'c-meas'], manual: ['نفّذه بإيدك', 'c-meas'],
      executed: [(d.exec_note || '').includes('تجربة') ? 'اتنفذ (تجربة)' : 'اتنفذ', 'c-done'],
      rejected: ['اترفض', 'c-fail'], refused: ['اتمنع', 'c-no'], failed: ['التنفيذ فشل', 'c-no'], measured: ['اتقاس', 'c-done'], executing: ['بيتنفذ', 'c-meas'],
    }[d.status] || [d.status, 'c-fail'];
    return chip(m[0], m[1]);
  }

  // ---------------------------------------------------------------- status & shell
  async function refreshStatus() {
    try { STATUS = await api('/api/status'); } catch (e) { return; }
    const p = STATUS.counts.pending, dr = STATUS.counts.drafts;
    const nbP = document.getElementById('nb-pending'), nbD = document.getElementById('nb-drafts');
    nbP.hidden = !p; nbP.textContent = p; nbD.hidden = !dr; nbD.textContent = dr;
    const side = document.getElementById('side-status');
    const lines = [
      h('div', null, h('span', { class: 'dot' + (STATUS.agents_enabled ? '' : ' off') }), STATUS.agents_enabled ? 'الفريق شغال' : 'الفريق متوقف'),
      h('div', null, h('span', { class: 'dot' + (STATUS.dry_run ? ' warn' : '') }), STATUS.dry_run ? 'وضع التجربة شغال' : 'بيغيّر في Meta فعلاً'),
      STATUS.running.length ? h('div', null, 'شغال دلوقتي: ' + STATUS.running.map(a => AG[a].name).join('، ')) : null,
      STATUS.last_run ? h('div', { class: 'muted' }, 'آخر تشغيل ' + dayOf(STATUS.last_run.finished_at) + ' ' + hm(STATUS.last_run.finished_at)) : null,
    ];
    side.replaceChildren(...lines.filter(Boolean));
    if (STATUS.account && STATUS.account.name) document.getElementById('brand-sub').textContent = STATUS.account.name;
  }
  function banners() {
    if (!STATUS) return null;
    const out = [];
    if (STATUS.update && STATUS.update.update_available) out.push(h('div', { class: 'banner info' },
      'فيه نسخة جديدة من البرنامج (' + STATUS.update.latest + '). اقفل الشباك الأسود، وروح لفولدر البرنامج ودوس دبل كليك على «تحديث.command». بياناتك والـtokens مش هتتلمس.'));
    if (STATUS.demo_flag) out.push(h('div', { class: 'banner info' }, 'ده وضع البيانات التجريبية: الإعلانات والعملا وهميين عشان تجرّب براحتك.'));
    else if (STATUS.demo) out.push(h('div', { class: 'banner warn' }, 'Meta لسه مش متوصّل، فأرقام الإعلانات اللي ظاهرة دي تجريبية. ', h('a', { href: '#setup' }, 'كمّل الإعداد')));
    if (!STATUS.agents_enabled) out.push(h('div', { class: 'banner bad' }, 'الفريق متوقف بالكامل من الإعدادات، ومفيش agent هيشتغل.'));
    else if (STATUS.dry_run && !STATUS.demo) out.push(h('div', { class: 'banner warn' }, 'وضع التجربة شغال: القرارات بتتسجل بس مفيش حاجة بتتبعت لـMeta. اقفله من الإعدادات لما تثق في القرارات.'));
    if (!STATUS.meta_ok) out.push(h('div', { class: 'banner bad' }, 'Meta: ' + STATUS.meta_error));
    return out;
  }
  function page(title, sub, actions, ...body) {
    const parts = [
      h('header', { class: 'head' }, h('div', null, h('h1', null, title), sub ? h('div', { class: 'sub' }, sub) : null), actions || null),
      ...(banners() || []), ...body.flat(Infinity)];
    main.replaceChildren(...parts.filter(x => x instanceof Node));
  }

  // ---------------------------------------------------------------- overview
  async function viewOverview() {
    const d = await api('/api/overview');
    const ads = d.ads || {};
    const rt = ads.result_type || 'purchase';
    const target = ads.target;
    const cpr = ads.cost_per_result;
    const runBtn = h('button', { class: 'btn pri', type: 'button' }, 'شغّل التيم اليومي دلوقتي');
    runBtn.addEventListener('click', busy(runBtn, async () => { await api('/api/run/daily', { method: 'POST' }); toast('بدأ التشغيل. تقدر تتابع في «الفريق والمحادثات».'); refreshStatus(); }));
    const kpis = h('section', { class: 'grid-kpi', 'aria-label': 'الأرقام الأساسية' },
      kpi('مبيعات امبارح', money(d.sales_yesterday.revenue), fmt(d.sales_yesterday.n) + ' طلب مسجّل'),
      kpi('صرف الإعلانات امبارح', d.meta_error ? '—' : money(ads.spend), 'السقف اليومي ' + money(d.daily_cap)),
      kpi('تكلفة ' + rtName(rt, true), money(cpr), target ? 'الهدف ' + money(target) : 'مفيش هدف متحدد', cpr == null || !target ? '' : (cpr <= target ? 'good' : 'bad')),
      rt === 'purchase'
        ? kpi('ROAS', fmt(ads.roas, 1), ads.only_main_goal ? 'من حملات البيع بس' : 'كل جنيه إعلانات رجّع كام مبيعات')
        : kpi('نتايج امبارح', fmt(ads.results), rtName(rt) + ' من الإعلانات'),
      kpi('متابعة النهارده', fmt(d.followups_today) + ' رسالة', 'جاهزة تتبعت من واتساب'),
      kpi('دقة القرارات', d.accuracy && d.accuracy.total ? fmt(d.accuracy.right) + ' من ' + fmt(d.accuracy.total) : '—', 'القرارات اللي اتقاست واتأكدت'),
      d.profit7 ? kpi('صافي الربح آخر 7 أيام', money(d.profit7.net_profit), 'بعد المنتج والشحن والمرتجع والإعلانات', d.profit7.net_profit == null ? '' : (d.profit7.net_profit >= 0 ? 'good' : 'bad')) : null,
    );
    const pending = h('section', { class: 'card', 'aria-labelledby': 'ap-h' },
      h('div', { class: 'head' }, h('h2', { id: 'ap-h' }, 'محتاج قرارك'), h('a', { href: '#decisions' }, 'كل القرارات')),
      d.pending.length ? d.pending.map(decisionMini) : h('div', { class: 'empty' }, 'مفيش حاجة مستنياك دلوقتي.'));
    const chart = h('section', { class: 'card', 'aria-labelledby': 'cpr-h' }, cprChart(d.series, target, rt, d.meta_error));
    const goals = (d.by_goal || []).length > 1 ? h('section', { class: 'card', 'aria-labelledby': 'goal-h' },
      h('h2', { id: 'goal-h' }, 'امبارح حسب هدف الحملة'),
      h('div', { class: 'small muted' }, 'كل نوع بيتقاس على هدفه هو. حملة فيديو مش بتتحاسب على المبيعات.'),
      h('div', { class: 'scroll' }, h('table', null,
        h('thead', null, h('tr', null, ['الهدف', 'حملات', 'الصرف', 'النتايج', 'التكلفة', 'الهدف المتحدد'].map(x => h('th', null, x)))),
        h('tbody', null, d.by_goal.map(g => h('tr', null,
          h('td', null, g.result_label, g.funnel === 'awareness' || g.funnel === 'traffic' ? h('span', { class: 'small muted' }, ' (مش بيجيب مبيعات مباشرة)') : null),
          h('td', { class: 'num' }, fmt(g.campaigns)), h('td', { class: 'num' }, money(g.spend)), h('td', { class: 'num' }, fmt(g.results)),
          h('td', { class: 'num ' + (g.cost_per_result != null && g.target ? (g.cost_per_result <= g.target ? 'good' : 'bad') : '') }, money(g.cost_per_result)),
          h('td', { class: 'num' }, g.target ? money(g.target) : '—'))))))) : null;
    const team = h('section', { 'aria-labelledby': 'team-h', class: 'card' },
      h('div', { class: 'head' }, h('h2', { id: 'team-h' }, 'الفريق'), h('a', { href: '#team' }, 'كل المحادثات')),
      h('div', { class: 'grid-kpi' }, d.team.map(teamCard)));
    const recent = h('section', { class: 'card', 'aria-labelledby': 'dec-h' }, h('h2', { id: 'dec-h' }, 'آخر القرارات'),
      d.recent.length ? d.recent.map(x => h('div', { class: 'list-row' },
        h('span', { class: 'num muted small' }, dayOf(x.created_at) + ' ' + hm(x.created_at)),
        h('span', { style: { flex: '1 1 260px' } }, x.title),
        h('span', { class: 'small muted' }, (MODE[x.mode] || MODE['—'])[0]), decisionChip(x))) : h('div', { class: 'empty' }, 'لسه مفيش قرارات.'));
    page('نظرة عامة', 'أرقام امبارح وحالة الفريق النهارده', runBtn, kpis, h('div', { class: 'row stretch' }, pending, chart), goals, team, recent);
  }
  // result types: [with article, plain]
  const RT = {
    purchase: ['البيعة', 'بيعات'], messages: ['المحادثة', 'محادثات'], leads: ['الـLead', 'Leads (فورم)'], calls: ['المكالمة', 'مكالمات'],
    registrations: ['التسجيل', 'تسجيلات'], landing_page_views: ['زيارة الصفحة', 'زيارات للصفحة'], link_clicks: ['الضغطة', 'ضغطات على الرابط'],
    engagement: ['التفاعل', 'تفاعلات'], video_views: ['المشاهدة', 'مشاهدات فيديو (3 ثواني)'], thruplays: ['الـThruPlay', 'ThruPlays'], page_likes: ['اللايك', 'لايكات للصفحة'], reach: ['الـ1000 وصول', 'وصول'],
  };
  const RT_FUNNEL = { purchase: 'sale', messages: 'lead', leads: 'lead', calls: 'lead', registrations: 'lead', landing_page_views: 'traffic',
    link_clicks: 'traffic', engagement: 'awareness', video_views: 'awareness', thruplays: 'awareness', page_likes: 'awareness', reach: 'awareness' };
  function rtName(t, withArticle) { const n = RT[t]; return n ? n[withArticle ? 0 : 1] : (withArticle ? 'النتيجة' : 'نتايج'); }
  function kpi(label, value, note, cls) {
    return h('div', { class: 'card kpi' }, h('div', { class: 'l' }, label), h('div', { class: 'v' }, value), h('div', { class: 'n ' + (cls || '') }, note));
  }
  function decisionMini(x) {
    const ok = h('button', { class: 'btn pri', type: 'button' }, 'وافق');
    const no = h('button', { class: 'btn', type: 'button' }, 'ارفض');
    ok.addEventListener('click', busy(ok, async () => { const r = await api('/api/decisions/' + x.id + '/approve', { method: 'POST' }); toast(r.status === 'failed' ? 'اتمنع وقت التنفيذ: ' + (r.reason || '') : 'تمام، اتنفذ.'); render(); }));
    no.addEventListener('click', busy(no, async () => { await api('/api/decisions/' + x.id + '/reject', { method: 'POST', body: { note: '' } }); toast('اترفض.'); render(); }));
    return h('div', { class: 'item' },
      h('div', { class: 'item-top' }, av(x.agent), h('div', { style: { flex: '1' } },
        h('div', { class: 't' }, x.title), h('div', { class: 'small muted' }, (AG[x.agent] || AG.system).name + ' · ' + x.reason),
        x.cost_note ? h('div', { class: 'small', style: { color: '#9A3F06' } }, x.cost_note) : null)),
      h('div', { class: 'btns' }, ok, no, h('a', { class: 'btn', href: '#decisions' }, 'التفاصيل')));
  }
  function cprChart(series, target, type, err) {
    const title = 'تكلفة ' + rtName(type, true) + ' آخر 14 يوم';
    const vals = (series || []).map(r => r.cost_per_result).filter(v => v !== null && v !== undefined);
    if (err || !vals.length) return [h('h2', null, title), h('div', { class: 'empty' }, err ? 'مش قادر أجيب الأرقام من Meta.' : 'لسه مفيش أرقام كفاية.')];
    const max = Math.max(...vals, target || 0) * 1.15, H = 200;
    const recent = vals.slice(-3), under = !!target && recent.every(v => v <= target);
    const box = h('div', { class: 'chart', role: 'img', 'aria-label': title });
    if (target) box.append(h('div', { class: 'target', style: { bottom: Math.round(target / max * H) + 'px' } }));
    for (const r of series) {
      const v = r.cost_per_result;
      box.append(h('div', { class: 'col', title: (r.date || '') + ': ' + fmt(v) },
        h('div', { class: 'lab' }, fmt(v)),
        h('div', { class: 'bar' + (target && v !== null && v > target ? ' over' : ''), style: { height: v === null ? '0px' : Math.max(2, Math.round(v / max * H)) + 'px' } })));
    }
    return [
      h('div', null, h('h2', null, under ? title + ': تحت الهدف آخر 3 أيام' : title), h('div', { class: 'small muted' }, (target ? 'الخط المتقطع هو الهدف (' + money(target) + ')' : 'مفيش هدف متحدد للنوع ده (حطه من الإعدادات)') + ' · الأحدث على الشمال')),
      box,
      h('div', { class: 'legend' }, h('span', null, h('span', { class: 'sw', style: { background: '#F0A36B' } }), 'فوق الهدف'), h('span', null, h('span', { class: 'sw', style: { background: '#1F4FD8' } }), 'تحت الهدف')),
    ];
  }
  function runChip(last, running) {
    if (running) return chip('شغال دلوقتي', 'c-meas');
    if (!last) return chip('لسه مشتغلش', 'c-fail');
    if (last.status === 'done') return chip('خلص ' + hm(last.finished_at), 'c-done');
    if (last.status === 'skipped') return chip('اتخطّى', 'c-fail');
    if (last.status === 'running') return chip('شغال', 'c-meas');
    return chip('وقف بخطأ', 'c-no');
  }
  function teamCard(t) {
    const btn = h('button', { class: 'btn', type: 'button', disabled: t.running }, 'شغّله دلوقتي');
    btn.addEventListener('click', busy(btn, async () => { await api('/api/agents/' + t.key + '/run', { method: 'POST' }); toast('بدأ ' + AG[t.key].name); refreshStatus(); }));
    const last = t.last;
    return h('div', { class: 'item' },
      h('div', { class: 'item-top' }, av(t.key), h('div', { style: { flex: '1' } }, h('div', { class: 't' }, AG[t.key].name), h('div', { class: 'small muted' }, t.schedule)), runChip(last, t.running)),
      h('div', { class: 'small pre' }, last ? clip(last.error || last.summary || '', 260) : 'لسه مشتغلش.'),
      last && last.turns ? h('div', { class: 'small muted num' }, fmt(last.turns) + ' خطوة' + (last.cost_usd ? ' · حوالي $' + fmt(last.cost_usd, 2) : '')) : null,
      h('div', { class: 'btns' }, btn, h('a', { class: 'btn', href: '#team' }, 'شوف عمل إيه')));
  }

  // ---------------------------------------------------------------- team
  async function viewTeam() {
    const day = ui.teamDay || todayStr();
    const [events, agent] = await Promise.all([api('/api/events?day=' + encodeURIComponent(day)), api('/api/agents/' + ui.teamAgent)]);
    const dayIn = h('input', { class: 'in', type: 'date', value: day, 'aria-label': 'اليوم', style: { width: 'auto' } });
    dayIn.addEventListener('change', () => { ui.teamDay = dayIn.value; render(); });
    const filters = [['all', 'الكل'], ['media_buyer', 'Media buyer'], ['followup', 'متابعة العملاء'], ['weekly_review', 'المراجعة الأسبوعية'], ['tools', 'الأدوات بس']];
    const pills = h('div', { class: 'btns', role: 'group', 'aria-label': 'فلتر' }, filters.map(([k, l]) => {
      const b = h('button', { class: 'pill' + (ui.teamFilter === k ? ' on' : ''), type: 'button', 'aria-pressed': String(ui.teamFilter === k) }, l);
      b.addEventListener('click', () => { ui.teamFilter = k; render(); });
      return b;
    }));
    const shown = events.filter(e => ui.teamFilter === 'all' || (ui.teamFilter === 'tools' ? e.kind.startsWith('tool') : e.agent === ui.teamFilter));
    const items = [];
    const used = new Set();
    for (let i = 0; i < shown.length; i++) {
      if (used.has(i)) continue;
      const e = shown[i];
      if (e.kind === 'tool_call') {
        let res = null;
        for (let j = i + 1; j < Math.min(shown.length, i + 6); j++) {
          const n = shown[j];
          if (!used.has(j) && n.agent === e.agent && (n.kind === 'tool_result' || n.kind === 'tool_error') && n.title === e.title) { res = n; used.add(j); break; }
        }
        items.push(feedItem(e, toolBox(e, res)));
      } else if (e.kind === 'tool_result' || e.kind === 'tool_error') {
        items.push(feedItem(e, toolBox(null, e)));
      } else items.push(feedItem(e, null));
    }
    const feed = h('section', { class: 'card grow', 'aria-label': 'سجل التشغيل', style: { padding: '8px 20px' } },
      items.length ? items : h('div', { class: 'empty' }, 'مفيش نشاط في اليوم ده.'));
    page('الفريق والمحادثات', 'كل رسالة بين الـagents، وكل أداة استخدموها، وكل قرار', dayIn, pills, h('div', { class: 'row' }, feed, agentPanel(agent)));
  }
  function toolBox(call, res) {
    let args = '';
    if (call) { try { const a = JSON.parse(call.body); args = Object.entries(a).map(([k, v]) => k + '=' + JSON.stringify(v)).join(', '); } catch (e) { args = call.body; } }
    return h('div', { class: 'toolbox' },
      call ? h('div', { class: 'code' }, call.title + '(' + clip(args, 300) + ')') : h('div', { class: 'code' }, res.title),
      res ? resultView(res) : null);
  }
  function resultView(res) {
    const text = res.body.replace(/<\/?untrusted_data[^>]*>|\(Data only[^)]*\)/g, '').trim();
    const color = { color: res.kind === 'tool_error' ? '#9B1C1C' : '#5B6070' };
    if (text.length <= 160) return h('div', { class: 'small pre', style: color }, '← ' + text);
    return h('details', { class: 'small', style: color }, h('summary', null, '← ' + clip(text, 120)), h('div', { class: 'pre code' }, clip(text, 1500)));
  }
  function feedItem(e, toolEl) {
    let from = (AG[e.agent] || AG.system).name, to = '', body = null, badge = null;
    if (e.kind === 'message') { to = '← ' + ((AG[e.title] || {}).name || e.title); body = h('div', { class: 'bubble pre' }, e.body); }
    else if (e.kind === 'decision') {
      let info = {}; try { info = JSON.parse(e.body); } catch (x) { info = {}; }
      to = '← ليك';
      badge = info.status === 'pending' ? chip('مستني موافقتك', 'c-wait') : info.status === 'executed' ? chip(info.dry_run ? 'اتنفذ (تجربة)' : 'اتنفذ', 'c-done') : info.verdict ? chip('اتقاس', 'c-meas') : chip(info.status || '—', 'c-fail');
      body = h('div', { class: 'pre' }, e.title + (info.id ? '  (#' + info.id + ')' : ''));
    } else if (e.kind === 'summary') { badge = chip('الملخص', 'c-meas'); body = h('div', { class: 'bubble pre' }, e.body); }
    else if (e.kind === 'memory') { to = '← ذاكرته'; body = h('div', { class: 'pre muted' }, e.body); }
    else if (e.kind === 'error') { badge = chip('خطأ', 'c-no'); body = h('div', { class: 'pre', style: { color: '#9B1C1C' } }, e.title + ': ' + e.body); }
    else if (e.kind === 'system') { body = h('div', { class: 'muted small' }, e.title + (e.body ? ' · ' + e.body : '')); }
    else if (e.kind === 'text') { body = h('div', { class: 'pre' }, e.body); }
    return h('div', { class: 'feed-item' }, h('div', { class: 'time' }, hm(e.created_at)), av(e.agent),
      h('div', { class: 'feed-body' }, h('div', { class: 'btns', style: { alignItems: 'center', gap: '6px 10px' } },
        h('strong', { class: 'small' }, from), to ? h('span', { class: 'small muted' }, to) : null, badge), toolEl || body));
  }
  function agentPanel(a) {
    const tabs = h('div', { class: 'btns', role: 'group', 'aria-label': 'اختار agent' }, ['media_buyer', 'followup', 'weekly_review'].map(k => {
      const b = h('button', { class: 'pill' + (ui.teamAgent === k ? ' on' : ''), type: 'button', 'aria-pressed': String(ui.teamAgent === k) }, AG[k].name);
      b.addEventListener('click', () => { ui.teamAgent = k; render(); });
      return b;
    }));
    const run = h('button', { class: 'btn pri', type: 'button', disabled: a.running }, a.running ? 'شغال دلوقتي…' : 'شغّله دلوقتي');
    run.addEventListener('click', busy(run, async () => { await api('/api/agents/' + a.key + '/run', { method: 'POST' }); toast('بدأ التشغيل'); setTimeout(render, 1500); }));
    return h('aside', { class: 'card aside', 'aria-labelledby': 'ag-h' }, h('h2', { id: 'ag-h' }, 'تفاصيل الـagent'), tabs,
      h('div', { class: 'item-top' }, av(a.key), h('div', null, h('div', { class: 't' }, AG[a.key].name), h('div', { class: 'small muted' }, a.schedule + ' · ' + a.model))),
      h('div', null, h('h3', null, 'بيعمل إيه ومن غير ما يسأل في إيه'), a.perms.map(p => h('div', { class: 'perm-row' }, h('span', null, p.what), chip(...(MODE[p.mode] || MODE['—']))))),
      h('div', null, h('h3', null, 'اللي فاكره'), a.memory.length ? a.memory.map(m => {
        const f = h('button', { class: 'pill', type: 'button', 'aria-label': 'انسى المعلومة دي' }, 'انسى');
        f.addEventListener('click', busy(f, async () => { await api('/api/memory/' + m.id, { method: 'DELETE' }); render(); }));
        return h('div', { class: 'list-row' }, h('span', { style: { flex: '1' }, class: 'small' }, m.fact), f);
      }) : h('div', { class: 'small muted' }, 'لسه مفتكرش حاجة.')),
      h('div', null, h('h3', null, 'آخر التشغيلات'), a.runs.length ? a.runs.map(r => h('div', { class: 'list-row small num' },
        h('span', { style: { flex: '1', whiteSpace: 'nowrap' } }, dayOf(r.started_at) + ' ' + hm(r.started_at)), runChip(r, false),
        h('span', { class: 'muted' }, (r.turns ? fmt(r.turns) + ' خطوة' : '') + (r.cost_usd ? ' · $' + fmt(r.cost_usd, 2) : '')))) : h('div', { class: 'small muted' }, 'لسه مشتغلش.')),
      run);
  }

  // ---------------------------------------------------------------- decisions
  async function viewDecisions() {
    const all = await api('/api/decisions?status=all');
    const groups = {
      pending: all.filter(d => d.status === 'pending'),
      measure: all.filter(d => (d.status === 'executed' || d.status === 'manual') && !d.verdict),
      done: all.filter(d => ['measured', 'rejected', 'failed', 'approved', 'executing'].includes(d.status)),
    };
    const labels = [['pending', 'مستني موافقتك'], ['measure', 'بنقيس'], ['done', 'خلص']];
    const tabs = h('div', { class: 'tabs', role: 'tablist' }, labels.map(([k, l]) => {
      const b = h('button', { class: 'tab' + (ui.decTab === k ? ' on' : ''), role: 'tab', 'aria-selected': String(ui.decTab === k), type: 'button' }, l + ' (' + groups[k].length + ')');
      b.addEventListener('click', () => { ui.decTab = k; render(); });
      return b;
    }));
    const right = all.filter(d => d.verdict === 'right').length, judged = all.filter(d => d.verdict === 'right' || d.verdict === 'wrong').length;
    const list = groups[ui.decTab];
    page('القرارات والموافقات', 'كل قرار معاه فرضية وميعاد قياس، عشان تعرف الـagents بيصيبوا قد إيه',
      judged ? chip('صح ' + right + ' من ' + judged, 'c-done') : null, tabs,
      h('section', { 'aria-label': 'القرارات', style: { display: 'flex', flexDirection: 'column', gap: '14px' } },
        list.length ? list.map(decisionCard) : h('div', { class: 'card empty' }, 'مفيش حاجة هنا.')));
  }
  function decisionCard(d) {
    const a = d.action || {};
    const kv = h('div', { class: 'kv' },
      h('div', null, h('b', null, 'ليه'), d.reason || '—'),
      h('div', null, h('b', null, 'الفرضية'), d.hypothesis || '—'),
      h('div', null, h('b', null, 'هنقيس إيه وإمتى'), (d.metric || '—') + (d.check_date ? ' · ' + d.check_date : '')),
      h('div', null, h('b', null, 'التكلفة / التغيير'), d.cost_note || '—'),
      a.result_label && (a.type === 'pause' || a.type === 'budget') ? h('div', null, h('b', null, 'هدف الحملة'), a.result_label) : null);
    const extra = [];
    if (a.type === 'proposal' && a.details) extra.push(h('div', { class: 'bubble pre small' }, a.details));
    if (a.type === 'mcp_call') extra.push(h('div', null, h('div', { class: 'small muted' }, 'اللي هيتبعت لـMeta بالظبط (أداة ' + a.tool + '):'), h('div', { class: 'bubble code pre' }, a.details || '')));
    if (d.status === 'pending') {
      const ok = h('button', { class: 'btn pri', type: 'button' }, a.type === 'proposal' ? 'موافق (هنفّذه بإيدي)' : 'وافق ونفّذ');
      const no = h('button', { class: 'btn', type: 'button' }, 'ارفض');
      const note = h('input', { class: 'in', type: 'text', placeholder: 'سبب الرفض (اختياري، الـagent هيتعلم منه)', maxlength: '300', 'aria-label': 'سبب الرفض' });
      ok.addEventListener('click', busy(ok, async () => { const r = await api('/api/decisions/' + d.id + '/approve', { method: 'POST' }); toast(r.status === 'failed' ? 'اتمنع وقت التنفيذ: ' + (r.reason || '') : r.status === 'manual' ? 'تمام. نفّذه في Ads Manager.' : 'اتنفذ.'); refreshStatus(); render(); }));
      no.addEventListener('click', busy(no, async () => { await api('/api/decisions/' + d.id + '/reject', { method: 'POST', body: { note: note.value } }); toast('اترفض.'); refreshStatus(); render(); }));
      extra.push(note, h('div', { class: 'btns' }, ok, no));
    }
    if (d.exec_note && d.status !== 'pending') extra.push(h('div', { class: 'small muted' }, d.exec_note));
    if ((d.status === 'executed' || d.status === 'manual') && !d.verdict && d.check_date) {
      const start = new Date(dayOf(d.executed_at || d.created_at)), end = new Date(d.check_date), now = new Date(todayStr());
      const total = Math.max(1, (end - start) / 864e5), done = Math.min(total, Math.max(0, (now - start) / 864e5));
      extra.push(h('div', null, h('div', { class: 'small num' }, 'يوم ' + Math.round(done) + ' من ' + Math.round(total) + ' · الـمراجعة الأسبوعية هتقيسه'),
        h('div', { class: 'progress', 'aria-hidden': 'true' }, h('i', { style: { width: Math.round(done / total * 100) + '%' } }))));
    }
    if (d.verdict) extra.push(h('div', { class: 'result ' + d.verdict }, h('strong', null, d.verdict === 'right' ? 'الفرضية صح: ' : d.verdict === 'wrong' ? 'الفرضية غلط: ' : 'مش واضح: '), d.outcome));
    return h('article', { class: 'card' },
      h('div', { class: 'item-top' }, av(d.agent), h('div', { style: { flex: '1' } },
        h('div', { class: 'btns', style: { alignItems: 'center', gap: '6px 10px' } }, h('h2', null, d.title), decisionChip(d)),
        h('div', { class: 'small muted' }, (AG[d.agent] || AG.system).name + ' · ' + dayOf(d.created_at) + ' ' + hm(d.created_at) + ' · ' + (MODE[d.mode] || MODE['—'])[0]))),
      kv, extra);
  }

  // ---------------------------------------------------------------- customers & orders
  async function viewCustomers() {
    const tabs = h('div', { class: 'tabs', role: 'tablist' }, [['followups', 'متابعة النهارده'], ['orders', 'الطلبات']].map(([k, l]) => {
      const b = h('button', { class: 'tab' + (ui.custTab === k ? ' on' : ''), role: 'tab', 'aria-selected': String(ui.custTab === k), type: 'button' }, l);
      b.addEventListener('click', () => { ui.custTab = k; render(); });
      return b;
    }));
    if (ui.custTab === 'orders') return viewOrders(tabs);
    const d = await api('/api/followups');
    const counts = { all: d.items.length };
    for (const it of d.items) counts[it.segment] = (counts[it.segment] || 0) + 1;
    const pills = h('div', { class: 'btns', role: 'group', 'aria-label': 'نوع المتابعة' }, [['all', 'الكل'], ...Object.entries(SEG).map(([k, v]) => [k, v[0]])].map(([k, l]) => {
      const b = h('button', { class: 'pill' + (ui.seg === k ? ' on' : ''), type: 'button', 'aria-pressed': String(ui.seg === k) }, l + ' (' + (counts[k] || 0) + ')');
      b.addEventListener('click', () => { ui.seg = k; render(); });
      return b;
    }));
    const items = d.items.filter(it => ui.seg === 'all' || it.segment === ui.seg);
    const left = d.items.filter(it => it.status === 'draft').length;
    const w = d.week;
    const aside = h('aside', { class: 'aside', style: { display: 'flex', flexDirection: 'column', gap: '16px' } },
      h('section', { class: 'card' }, h('h2', null, 'آخر 7 أيام'),
        [['رسايل متابعة اتبعتت', fmt(d.week_sent)], ['طلبات', fmt(w.orders)], ['مبيعات', money(w.revenue)], ['عملاء رجعوا اشتروا', fmt(w.returning_customers)], ['مرتجع', w.returned_pct == null ? '—' : w.returned_pct + '%']]
          .map(([l, v]) => h('div', { class: 'list-row' }, h('span', { class: 'muted small', style: { flex: '1' } }, l), h('strong', { class: 'num' }, v)))),
      h('section', { class: 'card small' }, h('h2', null, 'بتشتغل إزاي'),
        h('div', null, '«افتح واتساب» بيفتح المحادثة والرسالة مكتوبة، وانت تدوس إرسال بنفسك. مفيش API ومفيش فلوس.'),
        h('div', null, 'الـagent بيكتب مسودات بس، وأي رسالة فيها لينك أو رقم تليفون أو خصم مش في عروضك بتترفض تلقائي.')));
    page('متابعة العملاء', left + ' رسالة لسه متبعتتش', null, tabs,
      h('div', { class: 'row' }, h('section', { class: 'grow', style: { display: 'flex', flexDirection: 'column', gap: '12px' } }, pills,
        items.length ? items.map(it => followupCard(it, d.country_code)) : h('div', { class: 'card empty' }, 'مفيش رسايل هنا. الـagent بيجهزها كل صباح، أو شغّله من «نظرة عامة».')), aside));
  }
  function followupCard(it, cc) {
    const sent = it.status !== 'draft';
    const o = it.order || {};
    const ta = h('textarea', { rows: '3', 'aria-label': 'الرسالة لـ' + it.name, value: it.message, maxlength: '1000' });
    const wa = h('a', { class: 'btn wa', target: '_blank', rel: 'noopener noreferrer' }, 'افتح واتساب بالرسالة');
    const setHref = () => { wa.href = 'https://wa.me/' + encodeURIComponent(it.phone) + '?text=' + encodeURIComponent(ta.value); };
    setHref();
    ta.addEventListener('input', setHref);
    ta.addEventListener('change', async () => { try { await api('/api/followups/' + it.id, { method: 'POST', body: { message: ta.value } }); toast('اتحفظت'); } catch (e) { toast(e.message); } });
    const mark = h('button', { class: 'btn', type: 'button' }, sent ? 'رجّعها مش متبعتة' : 'علّم إنها اتبعتت');
    mark.addEventListener('click', busy(mark, async () => { await api('/api/followups/' + it.id, { method: 'POST', body: { status: sent ? 'draft' : 'sent' } }); refreshStatus(); render(); }));
    const skip = h('button', { class: 'btn', type: 'button' }, 'مش دلوقتي');
    skip.addEventListener('click', busy(skip, async () => { await api('/api/followups/' + it.id, { method: 'POST', body: { status: 'skipped' } }); refreshStatus(); render(); }));
    const ctx = o.product ? [o.product, money(o.amount), PAY[o.payment] || '', dayOf(o.created_at), o.source ? 'من ' + o.source : ''].filter(Boolean).join(' · ') : '';
    return h('article', { class: 'card' + (sent ? ' faded' : '') },
      h('div', { class: 'btns', style: { alignItems: 'center', gap: '6px 12px' } }, h('h2', null, it.name),
        h('span', { class: 'small muted num ltr' }, '+' + it.phone), chip(...SEG[it.segment]), it.status === 'sent' ? chip('اتبعتت', 'c-done') : it.status === 'skipped' ? chip('اتأجلت', 'c-fail') : null),
      ctx ? h('div', { class: 'small muted num' }, ctx) : null,
      ta, h('div', { class: 'btns' }, sent ? null : wa, mark, sent ? null : skip));
  }
  async function viewOrders(tabs) {
    const d = await api('/api/orders');
    const f = {
      name: h('input', { class: 'in', required: true, maxlength: '80' }), phone: h('input', { class: 'in ltr', required: true, inputmode: 'tel', maxlength: '20' }),
      product: h('input', { class: 'in', required: true, maxlength: '120' }), amount: h('input', { class: 'in', type: 'number', min: '0', step: '1', required: true }),
      payment: h('select', { class: 'in' }, h('option', { value: 'cod' }, PAY.cod), h('option', { value: 'online' }, PAY.online)),
      status: h('select', { class: 'in' }, Object.entries(d.statuses).map(([k, v]) => h('option', { value: k }, v))),
      source: h('input', { class: 'in', maxlength: '80', placeholder: 'اسم الإعلان أو «ماسنجر» أو «عميل قديم»' }),
    };
    const L = { name: 'اسم العميل', phone: 'رقم التليفون', product: 'المنتج', amount: 'المبلغ', payment: 'الدفع', status: 'الحالة', source: 'جه منين' };
    const save = h('button', { class: 'btn pri', type: 'submit' }, 'سجّل الطلب');
    const form = h('form', { class: 'card' }, h('h2', null, 'طلب جديد'),
      h('div', { class: 'g2' }, Object.entries(f).map(([k, el]) => h('label', { class: 'fld' }, L[k], el))), h('div', { class: 'btns' }, save));
    form.addEventListener('submit', busy(save, async () => {
      const body = {}; for (const [k, el] of Object.entries(f)) body[k] = el.value.trim();
      await api('/api/orders', { method: 'POST', body }); toast('اتسجل الطلب'); render();
    }));
    const file = h('input', { type: 'file', accept: '.csv,text/csv', 'aria-label': 'ملف CSV' });
    file.addEventListener('change', async () => {
      const fl = file.files[0]; if (!fl) return;
      if (fl.size > 5e6) { toast('الملف أكبر من 5 ميجا'); return; }
      try { const r = await api('/api/orders/import', { method: 'POST', body: { csv: await fl.text() } }); toast('اتضاف ' + r.imported + ' طلب' + (r.skipped_duplicates ? ' · ' + r.skipped_duplicates + ' كانوا موجودين' : '') + (r.errors.length ? ' · ' + r.errors.length + ' صف فيه مشكلة: ' + r.errors[0] : '')); render(); }
      catch (e) { toast(e.message); }
    });
    const imp = h('section', { class: 'card' }, h('h2', null, 'استورد من ملف'),
      h('div', { class: 'small muted' }, 'ملف CSV بالأعمدة: name, phone, product, amount، واختياري: payment (cod/online), status, source, created_at'), file);
    const table = h('div', { class: 'scroll' }, h('table', null,
      h('thead', null, h('tr', null, ['التاريخ', 'العميل', 'المنتج', 'المبلغ', 'الدفع', 'جه منين', 'الحالة'].map(x => h('th', null, x)))),
      h('tbody', null, d.items.map(o => {
        const sel = h('select', { class: 'in', 'aria-label': 'حالة الطلب', style: { minWidth: '110px' } }, Object.entries(d.statuses).map(([k, v]) => h('option', { value: k }, v)));
        sel.value = o.status;
        sel.addEventListener('change', async () => { try { await api('/api/orders/' + o.id + '/status', { method: 'POST', body: { status: sel.value } }); toast('اتحدّث'); } catch (e) { toast(e.message); } });
        return h('tr', null, h('td', { class: 'num' }, dayOf(o.created_at)), h('td', null, o.customer_name), h('td', null, o.product),
          h('td', { class: 'num' }, money(o.amount)), h('td', null, PAY[o.payment]), h('td', { class: 'small' }, o.source || '—'), h('td', null, sel));
      }))));
    page('متابعة العملاء', 'الطلبات بتتسجل هنا، والـagent بيعرف منها مين محتاج متابعة', null, tabs,
      h('div', { class: 'row' }, h('div', { class: 'grow' }, form), h('div', { class: 'aside' }, imp)),
      h('section', { class: 'card' }, h('h2', null, 'آخر الطلبات'), d.items.length ? table : h('div', { class: 'empty' }, 'لسه مفيش طلبات.')));
  }

  // ---------------------------------------------------------------- settings
  async function viewSettings() {
    const [{ values: v, meta, telegram: tg }, mcp] = await Promise.all([api('/api/settings'), api('/api/meta-mcp')]);
    const state = { ...v };
    const num = (key, unit, attrs = {}) => {
      const el = h('input', { type: 'number', value: v[key], step: 'any', ...attrs });
      el.addEventListener('input', () => { state[key] = el.value; });
      return unit ? h('div', { class: 'unit' }, el, h('span', null, unit)) : el;
    };
    const txt = (key, attrs = {}) => { const el = h('input', { class: 'in', type: 'text', value: v[key], ...attrs }); el.addEventListener('input', () => { state[key] = el.value; }); return el; };
    const area = (key) => { const el = h('textarea', { rows: '3', value: v[key] }); el.addEventListener('input', () => { state[key] = el.value; }); return el; };
    const sel = (key, opts) => { const el = h('select', { class: 'in' }, opts.map(([k, l]) => h('option', { value: k }, l))); el.value = v[key]; el.addEventListener('change', () => { state[key] = el.value; }); return el; };
    const sw = (key, label) => { const el = h('input', { type: 'checkbox', checked: v[key] }); el.addEventListener('change', () => { state[key] = el.checked; }); return h('label', { class: 'switch' }, el, label); };
    const time = (key) => { const el = h('input', { class: 'in', type: 'time', value: v[key], style: { width: '140px' } }); el.addEventListener('input', () => { state[key] = el.value; }); return el; };
    const seg = (key, choices, labels, colors) => {
      const box = h('div', { class: 'seg', role: 'radiogroup' });
      const draw = () => box.replaceChildren(...choices.map(c => {
        const on = state[key] === c;
        const b = h('button', { type: 'button', role: 'radio', 'aria-checked': String(on), class: on ? ((colors && colors[c]) || c) : '' }, labels ? labels[c] : MODE[c][0]);
        b.addEventListener('click', () => { state[key] = c; draw(); });
        return b;
      }));
      draw();
      return box;
    };
    state.targets = { ...(v.targets || {}) };
    const tnum = (t) => {
      const el = h('input', { type: 'number', value: state.targets[t] ?? '', step: 'any', min: '0', placeholder: 'من غير هدف', 'aria-label': 'هدف ' + RT[t][0] });
      el.addEventListener('input', () => { if (el.value === '') delete state.targets[t]; else state.targets[t] = el.value; });
      return h('div', { class: 'unit' }, el, h('span', null, C));
    };
    // Meta official MCP: per agent mode + per tool class
    state.mcp_overrides = { ...(v.mcp_overrides || {}) };
    const MCP_L = { off: 'مقفول', read: 'قراية بس', write_ask: 'قراية وكتابة' };
    const MCP_C = { off: 'no', read: 'auto', write_ask: 'ask' };
    for (const sh of ['mb', 'fu', 'wr']) if (state['mcp_' + sh] === 'write_auto') state['mcp_' + sh] = 'write_ask';
    const CLS_L = { read: 'قراية', write: 'كتابة بموافقتك', write_auto: 'كتابة أوتوماتيك', blocked: 'ممنوع' };
    const CLS_C = { read: 'auto', write: 'ask', write_auto: 'no', blocked: 'no' };
    const toolSeg = (t) => {
      const box = h('div', { class: 'seg', role: 'radiogroup', 'aria-label': t.name });
      const cur = () => state.mcp_overrides[t.name] || t.auto;
      const draw = () => box.replaceChildren(...['read', 'write', 'write_auto', 'blocked'].map(c => {
        const b = h('button', { type: 'button', role: 'radio', 'aria-checked': String(cur() === c), class: cur() === c ? CLS_C[c] : '' }, CLS_L[c]);
        b.addEventListener('click', () => { if (c === t.auto) delete state.mcp_overrides[t.name]; else state.mcp_overrides[t.name] = c; draw(); });
        return b;
      }));
      draw();
      return box;
    };
    const refreshBtn = h('button', { class: 'btn', type: 'button' }, 'حدّث قايمة الأدوات');
    refreshBtn.addEventListener('click', busy(refreshBtn, async () => { await api('/api/meta-mcp?refresh=true'); render(); }));
    const mcpSection = h('section', { class: 'card' }, h('h2', null, 'الـMCP الرسمي بتاع Meta'),
      h('div', { class: 'small muted' }, 'أدوات Meta الرسمية (mcp.facebook.com/ads) بتعدّي على البرنامج الأول، فكل استخدام ليها بيتسجل، وأي كتابة بتمشي على الموافقات ووضع التجربة ومفتاح الإيقاف.'),
      h('div', { class: 'banner ' + (mcp.connected ? 'info' : 'warn') }, mcp.connected
        ? (mcp.demo ? 'وضع تجريبي: دي أدوات شبه أدوات Meta عشان تجرّب.' : 'متوصّل · ' + mcp.tools.length + ' أداة')
        : (mcp.error || 'مش متوصّل') + ' · الخطوات في README'),
      sw('mcp_enabled', 'شغّل الـMCP الرسمي للفريق'),
      ...['mb', 'fu', 'wr'].map(sh => {
        const key = { mb: 'media_buyer', fu: 'followup', wr: 'weekly_review' }[sh];
        return h('div', { class: 'perm-row' }, h('div', { style: { flex: '1 1 200px' }, class: 'btns' }, av(key), h('strong', { style: { alignSelf: 'center' } }, AG[key].name)),
          seg('mcp_' + sh, ['off', 'read', 'write_ask'], MCP_L, MCP_C));
      }),
      h('div', { class: 'banner warn' }, 'أي كتابة بتستنى موافقتك، وانت بتشوف الطلب بالظبط زي ما هيتبعت لـMeta. «كتابة أوتوماتيك» بتختارها انت لأداة معينة، وحتى ساعتها بتتنفذ لوحدها التغييرات الآمنة بس (زي إنها تقفل حاجة). أي حاجة فيها budget أو صرف، أو بتعمل حاجة جديدة، أو بتشغّل حاجة مقفولة، بتستنى موافقتك. وأقصى 3 كتابات أوتوماتيك في التشغيل الواحد.'),
      mcp.tools.length ? h('div', null, h('h3', null, 'كل أداة: قراية ولا كتابة ولا ممنوعة'),
        h('div', { class: 'small muted' }, 'التصنيف التلقائي بيعتبر أي حاجة مش واضحة «كتابة» عشان تعدّي على موافقتك. تقدر تغيّره لكل أداة.'),
        mcp.tools.map(t => h('div', { class: 'perm-row' }, h('div', { style: { flex: '1 1 260px' } }, h('div', { class: 'code' }, t.name), h('div', { class: 'small muted' }, t.description)), toolSeg(t)))) : null,
      h('div', { class: 'btns' }, refreshBtn));
    const C = cur();
    const save = h('button', { class: 'btn pri', type: 'button' }, 'احفظ التغييرات');
    const save2 = h('button', { class: 'btn pri', type: 'button' }, 'احفظ التغييرات');
    const doSave = async () => { await api('/api/settings', { method: 'PUT', body: state }); toast('اتحفظ. هيتطبق من التشغيل الجاي.'); await refreshStatus(); render(); };
    save.addEventListener('click', busy(save, doSave)); save2.addEventListener('click', busy(save2, doSave));
    const permRow = (key, what, who, choices) => h('div', { class: 'perm-row' }, h('div', { style: { flex: '1 1 240px' } }, h('div', null, what), h('div', { class: 'small muted' }, who)), seg(key, choices));
    const days = ['الاتنين', 'التلات', 'الأربع', 'الخميس', 'الجمعة', 'السبت', 'الحد'];
    const models = [['sonnet', 'Sonnet'], ['opus', 'Opus'], ['haiku', 'Haiku']];
    const agentRow = (short, key, when) => h('div', { class: 'perm-row' }, av(key), h('strong', { style: { flex: '1 1 160px' } }, AG[key].name), when,
      h('label', { class: 'fld' }, 'الموديل', sel('model_' + short, models)), sw('enabled_' + short, 'شغال'));
    page('الإعدادات', 'الـagents بيقروا الإعدادات دي أول كل تشغيل', save,
      h('section', { class: 'card' }, h('h2', null, 'الأمان'),
        sw('agents_enabled', 'الفريق شغال (اقفلها عشان توقف كل الـagents فوراً)'),
        sw('dry_run', 'وضع التجربة: القرارات بتتسجل بس مفيش حاجة بتتبعت لـMeta'),
        h('label', { class: 'fld' }, 'أقصى تكلفة تقريبية للتشغيل الواحد', num('max_usd_per_run', 'دولار', { min: 0.05, max: 20 }), h('small', null, 'لو التشغيل وصل للرقم ده بيقف لوحده'))),
      (() => {
        const test = h('button', { class: 'btn', type: 'button' }, 'ابعت رسالة تجربة');
        test.addEventListener('click', busy(test, async () => { await api('/api/telegram/test', { method: 'POST' }); toast('اتبعتت. شوف Telegram.'); }));
        return h('section', { class: 'card' }, h('h2', null, 'Telegram'),
          h('div', { class: 'banner ' + (tg.has_token && tg.has_chat ? 'info' : 'warn') }, tg.has_token && tg.has_chat
            ? 'البوت متظبط. الموافقات بتوصلك بزرارين، والأوامر: /status و /pending و /stop.'
            : 'Telegram مش متوصّل لسه. وصّله من صفحة «الإعداد أول مرة» (3 دقايق).'),
          sw('telegram_enabled', 'شغّل Telegram'),
          sw('telegram_brief', 'ابعتلي ملخص كل agent لما يخلص'),
          sw('telegram_approvals', 'ابعتلي القرارات اللي محتاجة موافقة بزرار «وافق» و«ارفض»'),
          h('div', { class: 'small muted' }, 'البوت بيرد عليك انت بس (الحساب اللي ربطته في الإعداد)، وأي حد تاني بيتجاهله. و/stop بيوقف الفريق كله فوراً.'),
          h('div', { class: 'btns' }, test));
      })(),
      h('section', { class: 'card' }, h('h2', null, 'Meta Ads'),
        h('div', { class: 'banner ' + (meta.connected ? 'info' : 'warn') }, meta.connected
          ? 'متوصّل بحساب ' + meta.account_id + ' · Graph API ' + meta.graph_version
          : 'مش متوصّل لسه. وصّله من صفحة «الإعداد أول مرة».'),
        h('div', { class: 'small muted' }, 'الـtoken متخزن على اللابتوب ده بس، ومبيوصلش للـagents ولا بيظهر في الصفحة.'),
        h('div', { class: 'btns' }, h('a', { class: 'btn', href: '#setup' }, meta.connected ? 'غيّر الربط' : 'وصّل Meta'))),
      h('section', { class: 'card' }, h('h2', null, 'البيزنس'),
        h('div', { class: 'g2' }, h('label', { class: 'fld' }, 'اسم البراند', txt('brand_name', { maxlength: '80' })),
          h('label', { class: 'fld' }, 'طريقة الدفع', sel('payment_methods', [['cod', 'كاش عند الاستلام'], ['online', 'أونلاين'], ['both', 'الاتنين']])),
          h('label', { class: 'fld' }, 'كود الدولة للتليفونات', txt('country_code', { maxlength: '4', inputmode: 'numeric' })),
          h('label', { class: 'fld' }, 'العميل بيرجع يشتري بعد', num('repeat_days', 'يوم', { min: 7, max: 365 })),
          h('label', { class: 'fld' }, 'يتحسب «بقاله فترة» بعد', num('lapsed_days', 'يوم', { min: 14, max: 730 })))),
      h('section', { class: 'card' }, h('h2', null, 'الأرقام والحدود'),
        h('div', { class: 'small muted' }, 'الحدود دي متكتبة في كود الأدوات نفسها، فالـagent مش هيقدر يحط budgets أعلى منها مهما حصل. بس Meta نفسها ممكن تصرف أكتر من الـdaily budget في بعض الأيام، فحط كمان «حد صرف للحساب» (Account spending limit) من Billing في Meta كحماية أخيرة.'),
        h('div', { class: 'g2' },
          h('label', { class: 'fld' }, 'كل حملة بتتقاس على إيه', sel('result_type', [['auto', 'على هدفها هي (مستحسن)'], ...Object.keys(RT).map(t => [t, 'كلها على ' + rtName(t)])]),
            h('small', null, '«على هدفها هي» يعني حملة الرسايل بتتقاس بالرسايل، وحملة الفيديو بالمشاهدات، وحملة البيع بالبيعات.')),
          h('label', { class: 'fld' }, 'تكلفة البيعة المستهدفة', num('target_cpr', C, { min: 1 })),
          h('label', { class: 'fld' }, 'أقصى صرف في اليوم', num('daily_cap', C, { min: 1 })),
          h('label', { class: 'fld' }, 'أقصى صرف في الشهر', num('monthly_cap', C, { min: 1 })),
          h('label', { class: 'fld' }, 'أقصى زيادة budget في المرة', num('max_increase_pct', '%', { min: 1, max: 100 })),
          h('label', { class: 'fld' }, 'أقصى تقليل budget في المرة', num('max_decrease_pct', '%', { min: 5, max: 90 })),
          h('label', { class: 'fld' }, 'أقل وقت بين تغييرين لنفس الـbudget', num('budget_cooldown_hours', 'ساعة', { min: 0, max: 336 })))),
      h('section', { class: 'card' }, h('h2', null, 'التكلفة المستهدفة للإعلانات اللي مش بيع'),
        h('div', { class: 'small muted' }, 'حط هدف بس للأنواع اللي بتعملها. لو سبت نوع فاضي، الـagent مش هيقفل ولا يقلل حملة من النوع ده بسبب تكلفتها، وهيقترح عليك هدف بالأرقام. وحملات التفاعل والمشاهدات والوصول عمرها ما بتتغير من غير موافقتك.'),
        h('div', { class: 'small', style: { fontWeight: '600', marginTop: '8px' } }, 'بتجيب عملا'),
        h('div', { class: 'g2' }, ['messages', 'leads', 'calls', 'registrations'].map(t => h('label', { class: 'fld' }, 'تكلفة ' + RT[t][0], tnum(t)))),
        h('div', { class: 'small', style: { fontWeight: '600', marginTop: '8px' } }, 'زيارات وتفاعل ووصول (مش بيجيبوا مبيعات مباشرة)'),
        h('div', { class: 'g2' }, ['landing_page_views', 'link_clicks', 'engagement', 'video_views', 'thruplays', 'page_likes', 'reach'].map(t => h('label', { class: 'fld' }, 'تكلفة ' + RT[t][0], tnum(t))))),
      h('section', { class: 'card' }, h('h2', null, 'تكاليف الطلب (عشان نحسب الربح الحقيقي)'),
        h('div', { class: 'small muted' }, 'تكلفة كل منتج بالظبط تتحط في «الربح والتحليل». النسبة هنا للمنتجات اللي ملهاش تكلفة متسجلة.'),
        h('div', { class: 'g2' },
          h('label', { class: 'fld' }, 'تكلفة المنتج كنسبة من سعره', num('cogs_pct', '%', { min: 0, max: 95 })),
          h('label', { class: 'fld' }, 'تكلفة الشحن للطلب', num('shipping_cost', C, { min: 0 })),
          h('label', { class: 'fld' }, 'خسارة الطلب المرتجع', num('return_cost', C, { min: 0 }), h('small', null, 'شحن رايح وجاي وأي تلف')),
          h('label', { class: 'fld' }, 'عايز تكسب كام من هامش الطلب', num('target_profit_share', '%', { min: 0, max: 90 }), h('small', null, 'بنحسب منها التكلفة المستهدفة المقترحة')))),
      h('section', { class: 'card' }, h('h2', null, 'قواعد الـMedia buyer'),
        h('div', { class: 'rule' }, 'اقفل لو صرف', num('rule_pause_multiple', null, { min: 1, max: 10, 'aria-label': 'مضاعف الهدف' }), 'أضعاف التكلفة المستهدفة من غير ولا نتيجة'),
        h('div', { class: 'rule' }, 'زوّد الـbudget لو الـROAS فوق', num('rule_scale_roas', null, { min: 0.5, max: 50, 'aria-label': 'حد الـROAS' }), 'لمدة', num('rule_scale_days', null, { min: 1, max: 14, 'aria-label': 'عدد الأيام' }), 'أيام'),
        h('div', { class: 'rule' }, 'اقترح إعلانات جديدة لو الـfrequency عدّت', num('rule_fatigue_freq', null, { min: 1, max: 20, 'aria-label': 'حد الـfrequency' }), 'والـCTR نزل أكتر من', num('rule_fatigue_ctr_drop', null, { min: 5, max: 90, 'aria-label': 'نسبة نزول الـCTR' }), '%')),
      h('section', { class: 'card' }, h('h2', null, 'مين يقرر إيه'),
        h('div', { class: 'small muted' }, 'ابدأ بـ«بموافقتك» في كل حاجة، وحوّل لـ«أوتوماتيك» لما تشوف إن القرارات صح.'),
        permRow('perm_pause', 'قفل ad set أو حملة', 'Media buyer والمراجعة الأسبوعية', ['auto', 'ask', 'no']),
        permRow('perm_budget_up', 'زيادة budget (في حدود الزيادة القصوى والسقف)', 'Media buyer والمراجعة الأسبوعية', ['auto', 'ask', 'no']),
        permRow('perm_budget_down', 'تقليل budget', 'Media buyer والمراجعة الأسبوعية', ['auto', 'ask', 'no']),
        permRow('perm_new_campaign', 'اقتراح حملة جديدة', 'انت اللي بتعملها في Ads Manager', ['ask', 'no']),
        permRow('perm_new_creative', 'اقتراح إعلانات جديدة', 'انت اللي بتعملها في Ads Manager', ['ask', 'no']),
        h('div', { class: 'perm-row' }, h('div', null, 'تعدّي السقف اليومي أو الشهري'), chip('ممنوع دايماً', 'c-no')),
        h('div', { class: 'perm-row' }, h('div', null, 'إرسال رسالة لعميل'), chip('انت بتبعت من واتساب', 'c-wait'))),
      mcpSection,
      h('section', { class: 'card' }, h('h2', null, 'الفريق والمواعيد'),
        agentRow('mb', 'media_buyer', h('label', { class: 'fld' }, 'كل يوم', time('time_mb'))),
        agentRow('fu', 'followup', h('label', { class: 'fld' }, 'كل يوم', time('time_fu'))),
        agentRow('wr', 'weekly_review', h('div', { class: 'btns' }, h('label', { class: 'fld' }, 'اليوم', sel('day_wr', days.map((d, i) => [String(i), d]))), h('label', { class: 'fld' }, 'الساعة', time('time_wr'))))),
      h('section', { class: 'card' }, h('h2', null, 'صوت البراند والعروض'),
        h('label', { class: 'fld' }, 'إزاي البراند بيتكلم', area('brand_voice')),
        h('label', { class: 'fld' }, 'العروض الشغالة دلوقتي', area('offers'), h('small', null, 'الـagents مش هيكتبوا أي خصم أو حاجة مجانية مش مكتوبة هنا. سيبها فاضية لو مفيش عروض.'))),
      h('div', { class: 'btns' }, save2));
  }

  // ---------------------------------------------------------------- profit & analysis
  ui.pDays = 7; ui.bdDim = 'age_gender'; ui.bdPeriod = 'last_14d';
  const DIMS = [['age_gender', 'السن والنوع'], ['age', 'السن'], ['gender', 'النوع'], ['placement', 'مكان الظهور'], ['region', 'المحافظة'], ['hour', 'الساعة']];
  async function viewProfit() {
    const [p, products] = await Promise.all([api('/api/profit?days=' + ui.pDays), api('/api/products')]);
    STATE_TARGET = p.current_target_cpr;
    let bd = null, fat = [], bdErr = '';
    try { [bd, fat] = await Promise.all([api('/api/breakdown?dimension=' + ui.bdDim + '&period=' + ui.bdPeriod), api('/api/fatigue')]); } catch (e) { bdErr = e.message; }
    const pills = (items, cur, set) => h('div', { class: 'btns', role: 'group' }, items.map(([k, l]) => {
      const b = h('button', { class: 'pill' + (cur === k ? ' on' : ''), type: 'button', 'aria-pressed': String(cur === k) }, l);
      b.addEventListener('click', () => { set(k); render(); });
      return b;
    }));
    const be = p.breakeven_cost_per_order;
    const warn = p.target_above_breakeven
      ? h('div', { class: 'banner bad' }, 'التكلفة المستهدفة في الإعدادات (' + money(p.current_target_cpr) + ') أعلى من نقطة التعادل (' + money(be) + '). يعني كل بيعة بالسعر ده بتخسّرك. المقترح: ' + money(p.suggested_target_cpr) + '.')
      : (p.suggested_target_cpr ? h('div', { class: 'banner info' }, 'نقطة التعادل ' + money(be) + ' للطلب. عشان تكسب ' + p.days + ' أيام زي دي، خلي التكلفة المستهدفة حوالي ' + money(p.suggested_target_cpr) + ' (الحالية ' + money(p.current_target_cpr) + ').') : null);
    const pr = p.per_result;
    const perResult = pr ? h('div', { class: 'banner ' + (pr.target_above_breakeven ? 'bad' : 'info') },
      'أغلب إعلاناتك بتجيب ' + rtName(pr.result_type) + '. من الطلبات اللي سجّلتها، كل طلب محتاج حوالي ' + fmt(pr.results_per_order, 1) + ' ' + rtName(pr.result_type) + '. ',
      'يعني ' + rtName(pr.result_type, true) + ' بتستاهل لحد ' + money(pr.breakeven_cost_per_result) + ' (نقطة التعادل). ',
      pr.current_target ? (pr.target_above_breakeven ? 'الهدف الحالي (' + money(pr.current_target) + ') أعلى من كده وبيخسّرك. ' : 'الهدف الحالي ' + money(pr.current_target) + '. ') : 'لسه محطتش هدف للنوع ده. ',
      'المقترح: ' + money(pr.suggested_target) + '. ', h('a', { href: '#settings' }, 'غيّره من الإعدادات'))
      : (p.ads_main_result && RT_FUNNEL[p.ads_main_result] === 'lead' ? h('div', { class: 'banner info' }, 'أغلب إعلاناتك بتجيب ' + rtName(p.ads_main_result) + '. سجّل الطلبات اللي بتقفلها (ومنين جت) في «متابعة العملاء ‹ الطلبات»، وهنحسبلك ' + rtName(p.ads_main_result, true) + ' بتستاهل كام بالفلوس.') : null);
    const kpis = h('section', { class: 'grid-kpi', 'aria-label': 'الربح' },
      p.real_cost_per_order != null ? kpi('تكلفة الطلب الحقيقية', money(p.real_cost_per_order), 'صرف الإعلانات ÷ الطلبات المسجّلة', be != null ? (p.real_cost_per_order <= be ? 'good' : 'bad') : '') : null,
      kpi('صافي الربح', money(p.net_profit), p.ad_error ? 'مش قادر أجيب صرف الإعلانات' : 'بعد الإعلانات (' + money(p.ad_spend) + ')', p.net_profit == null ? '' : (p.net_profit >= 0 ? 'good' : 'bad')),
      kpi('الربح قبل الإعلانات', money(p.gross_before_ads), fmt(p.orders) + ' طلب'),
      kpi('ربح الطلب قبل الإعلانات', money(p.margin_per_order_before_ads), 'ده أقصى تكلفة بيعة للتعادل'),
      kpi('المرتجع', p.return_pct == null ? '—' : p.return_pct + '%', money(p.returns_cost) + ' تكلفة مرتجعات', p.return_pct > 10 ? 'bad' : ''),
      kpi('تكلفة المنتجات', money(p.product_cost), p.orders_with_estimated_cost ? fmt(p.orders_with_estimated_cost) + ' طلب بتكلفة تقديرية' : 'كلها من جدول المنتجات'));
    const srcTable = h('div', { class: 'scroll' }, h('table', null,
      h('thead', null, h('tr', null, ['جه منين', 'طلبات', 'مبيعات', 'مرتجع', 'ربح قبل الإعلانات', 'ربح الطلب'].map(x => h('th', null, x)))),
      h('tbody', null, p.by_source.map(r => h('tr', null, h('td', null, r.source), h('td', { class: 'num' }, fmt(r.orders)), h('td', { class: 'num' }, money(r.revenue)),
        h('td', { class: 'num' }, r.return_pct == null ? '—' : r.return_pct + '%'), h('td', { class: 'num' }, money(r.margin)), h('td', { class: 'num' }, money(r.margin_per_order)))))));
    // products
    const pn = h('input', { class: 'in', placeholder: 'اسم المنتج زي ما بتكتبه في الطلبات', maxlength: '120', 'aria-label': 'اسم المنتج' });
    const pc = h('input', { class: 'in', type: 'number', min: '0', step: 'any', placeholder: 'التكلفة', 'aria-label': 'تكلفة المنتج' });
    const add = h('button', { class: 'btn pri', type: 'submit' }, 'احفظ');
    const pform = h('form', { class: 'btns' }, h('div', { style: { flex: '2 1 200px' } }, pn), h('div', { style: { flex: '1 1 100px' } }, pc), add);
    pform.addEventListener('submit', busy(add, async () => { await api('/api/products', { method: 'POST', body: { name: pn.value, unit_cost: pc.value } }); toast('اتحفظ'); render(); }));
    const prodCard = h('section', { class: 'card' }, h('h2', null, 'تكلفة المنتجات'),
      h('div', { class: 'small muted' }, 'اكتب اسم المنتج بالظبط زي ما بيتسجل في الطلبات. أي منتج مش هنا بتتحسب تكلفته بالنسبة اللي في الإعدادات.'),
      pform, products.map(x => {
        const del = h('button', { class: 'pill', type: 'button', 'aria-label': 'امسح ' + x.name }, 'امسح');
        del.addEventListener('click', busy(del, async () => { await api('/api/products/' + x.id, { method: 'DELETE' }); render(); }));
        return h('div', { class: 'list-row' }, h('span', { style: { flex: '1' } }, x.name), h('strong', { class: 'num' }, money(x.unit_cost)), del);
      }));
    // breakdown
    let bdCard;
    if (bdErr) bdCard = h('section', { class: 'card' }, h('h2', null, 'مين بيشتري ومنين'), h('div', { class: 'banner bad' }, bdErr));
    else {
      const rows = bd.rows;
      const metric = bd.results_available ? 'cost_per_result' : 'ctr_pct';
      const max = Math.max(...rows.map(r => r[metric] || 0), 1);
      bdCard = h('section', { class: 'card' }, h('h2', null, 'مين بيشتري ومنين'),
        pills(DIMS, ui.bdDim, k => { ui.bdDim = k; }),
        pills([['last_7d', '7 أيام'], ['last_14d', '14 يوم'], ['last_30d', '30 يوم']], ui.bdPeriod, k => { ui.bdPeriod = k; }),
        h('div', { class: 'small muted' }, 'محسوبة على ' + rtName(bd.result_type) + ' (الهدف اللي أغلب الميزانية رايحة له).'),
        bd.results_available ? null : h('div', { class: 'small muted' }, 'Meta مبتديش نتايج الموقع للتقسيمة دي، فبنقارن بنسبة الضغط (CTR) ونصيب الصرف.'),
        h('div', { class: 'scroll' }, h('table', null,
          h('thead', null, h('tr', null, ['الشريحة', 'نصيب الصرف', bd.results_available ? 'تكلفة ' + rtName(bd.result_type, true) : 'CTR', '', 'النتايج'].map(x => h('th', null, x)))),
          h('tbody', null, rows.map(r => {
            const v = r[metric];
            const over = bd.results_available && v != null && v > (bd.target_cost_per_result || Infinity);
            return h('tr', null, h('td', { class: 'ltr', style: { textAlign: 'right' } }, r.segment), h('td', { class: 'num' }, r.spend_share_pct + '%'),
              h('td', { class: 'num' }, bd.results_available ? (v == null ? 'مفيش' : money(v)) : (v + '%')),
              h('td', { style: { width: '35%' } }, h('div', { class: 'progress' }, h('i', { style: { width: (v ? Math.round(v / max * 100) : 0) + '%', background: over ? '#F0A36B' : '#1F4FD8' } }))),
              h('td', { class: 'num' }, bd.results_available ? fmt(r.results) : '—'));
          })))));
    }
    const fatCard = h('section', { class: 'card' }, h('h2', null, 'الإعلانات اللي الناس زهقت منها'),
      h('div', { class: 'small muted' }, 'الضغط (CTR) آخر 3 أيام مقارنة بآخر 14 يوم، وعدد مرات المشاهدة للشخص.'),
      fat.length ? h('div', { class: 'scroll' }, h('table', null,
        h('thead', null, h('tr', null, ['الإعلان', 'CTR آخر 3 أيام', 'CTR آخر 14 يوم', 'النزول', 'Frequency', ''].map(x => h('th', null, x)))),
        h('tbody', null, fat.map(r => h('tr', null, h('td', null, r.ad_name), h('td', { class: 'num' }, r.ctr_last_3d + '%'), h('td', { class: 'num' }, r.ctr_last_14d + '%'),
          h('td', { class: 'num' }, r.ctr_drop_pct + '%'), h('td', { class: 'num' }, fmt(r.frequency_7d, 1)), h('td', null, r.tired ? chip('محتاج إعلان جديد', 'c-wait') : chip('تمام', 'c-done'))))))) : h('div', { class: 'empty' }, 'مفيش بيانات كفاية.'));
    page('الربح والتحليل', 'الفلوس اللي فضلت معاك فعلاً، ومين بيشتري ومنين', pills([[7, '7 أيام'], [14, '14 يوم'], [30, '30 يوم']], ui.pDays, k => { ui.pDays = k; }),
      warn, perResult, kpis, h('div', { class: 'row' }, h('section', { class: 'card grow' }, h('h2', null, 'الربح حسب مصدر الطلب'), p.by_source.length ? srcTable : h('div', { class: 'empty' }, 'لسه مفيش طلبات في الفترة دي.')), h('div', { class: 'aside' }, prodCard)),
      bdCard, fatCard);
  }
  let STATE_TARGET = null;

  // ---------------------------------------------------------------- first-run setup
  // Guides are plain text with **bold** marks; rendered with textContent only.
  function steps(items) {
    return h('ol', null, items.map(t => h('li', null, t.split(/(\*\*[^*]+\*\*)/).map(part =>
      part.startsWith('**') ? h('b', null, part.slice(2, -2)) : part))));
  }
  function guide(title, items, open, tip) {
    return h('details', { class: 'guide', open: open || null }, h('summary', null, title), steps(items), tip ? h('div', { class: 'tip' }, tip) : null);
  }
  function link(href, text) { return h('a', { href, target: '_blank', rel: 'noopener noreferrer' }, text); }
  function stepCard(n, title, done, optional, ...body) {
    return h('section', { class: 'card' },
      h('div', { class: 'item-top' }, h('div', { class: 'stepno' + (done ? ' done' : '') }, done ? '✓' : String(n)),
        h('div', { style: { flex: '1' } }, h('h2', null, title), optional ? h('div', { class: 'small muted' }, 'اختياري') : null),
        done ? chip('تمام', 'c-done') : chip('لسه', optional ? 'c-fail' : 'c-wait')), ...body);
  }
  const secret = (label, id, ph) => h('label', { class: 'fld' }, label,
    h('input', { class: 'in ltr', id, type: 'password', autocomplete: 'off', spellcheck: 'false', placeholder: ph || '' }));
  const val = (id) => (document.getElementById(id) || {}).value || '';
  let tgPoll = null;

  async function viewSetup() {
    const st = await api('/api/setup');
    clearInterval(tgPoll);
    // 1. Claude
    const cBtn = h('button', { class: 'btn pri', type: 'button' }, 'جرّب Claude');
    const cOut = h('div', { class: 'small' });
    cBtn.addEventListener('click', busy(cBtn, async () => {
      cOut.textContent = 'بيجرّب… (ممكن ياخد دقيقة)';
      const r = await api('/api/setup/claude-test', { method: 'POST' });
      cOut.replaceChildren(r.ok ? h('div', { class: 'banner info' }, 'Claude شغال على الجهاز ده.') : h('div', { class: 'banner bad' }, r.error));
    }));
    const s1 = stepCard(1, 'Claude', false, false,
      h('div', null, 'الفريق بيشتغل باشتراكك في Claude. دوس الزرار نتأكد إن Claude شغال على الجهاز ده.'),
      guide('لو قالك «مش متسجّل دخول»', [
        'اقفل الصفحة دي وارجع لفولدر المشروع.',
        'دوس دبل كليك على **تسجيل دخول Claude.command**. (أول مرة بس: كليك يمين ← **Open** ← **Open**، عشان الماك يسمح بيه)',
        'هتفتح نافذة سودا وهتطلب منك تسجّل دخول. اختار حساب **Claude** (اشتراك Pro أو Max) وكمّل في المتصفح.',
        'لما يقولك تمام، اقفل النافذة السودا وارجع هنا ودوس **جرّب Claude** تاني.',
      ], false), h('div', { class: 'btns' }, cBtn), cOut);

    // 2. Business
    const v = st.values;
    const bf = {
      brand_name: h('input', { class: 'in', value: v.brand_name === 'براندك' ? '' : v.brand_name, maxlength: '80', placeholder: 'مثلاً: Nour Wear أو عيادة د. سارة' }),
      payment_methods: h('select', { class: 'in' }, [['cod', 'كاش عند الاستلام'], ['online', 'أونلاين'], ['both', 'الاتنين']].map(([k, l]) => h('option', { value: k }, l))),
      shipping_cost: h('input', { class: 'in', type: 'number', min: '0', value: v.shipping_cost }),
      daily_cap: h('input', { class: 'in', type: 'number', min: '1', value: v.daily_cap }),
      monthly_cap: h('input', { class: 'in', type: 'number', min: '1', value: v.monthly_cap }),
    };
    bf.payment_methods.value = v.payment_methods;
    // what the ads mostly bring: decides which target we ask for (every campaign is still judged by its own goal)
    const GOALS = [['purchase', 'أوردرات أونلاين (بيع من الموقع)'], ['messages', 'رسايل واتساب أو ماسنجر'], ['leads', 'فورم بيانات (Leads)'],
      ['calls', 'مكالمات'], ['registrations', 'تسجيل على موقع'], ['awareness', 'تفاعل ومتابعين ومشاهدات بس']];
    const goalSel = h('select', { class: 'in' }, GOALS.map(([k, l]) => h('option', { value: k }, l)));
    const tg0 = v.targets || {};
    goalSel.value = ['messages', 'leads', 'calls', 'registrations'].find(k => tg0[k]) || 'purchase';
    const tgtIn = h('input', { class: 'in', type: 'number', min: '0', step: 'any' });
    const tgtLabel = h('span');
    const tgtBox = h('label', { class: 'fld' }, tgtLabel, tgtIn);
    const syncGoal = () => {
      const g = goalSel.value;
      tgtBox.hidden = g === 'awareness';
      tgtLabel.textContent = 'عايز ' + (g === 'purchase' ? 'البيعة' : rtName(g, true)) + ' تكلفك إعلانات كام';
      tgtIn.value = g === 'purchase' ? v.target_cpr : (tg0[g] ?? '');
    };
    goalSel.addEventListener('change', syncGoal);
    syncGoal();
    const bSave = h('button', { class: 'btn pri', type: 'button' }, 'احفظ');
    bSave.addEventListener('click', busy(bSave, async () => {
      const b = {}; for (const [k, el] of Object.entries(bf)) b[k] = el.value;
      if (!b.brand_name.trim()) throw new Error('اكتب اسم البيزنس');
      const g = goalSel.value;
      if (g === 'purchase') b.target_cpr = tgtIn.value;
      else if (g !== 'awareness') b.targets = { ...tg0, [g]: tgtIn.value };
      b.result_type = 'auto';
      await api('/api/settings', { method: 'PUT', body: b }); toast('اتحفظ'); render();
    }));
    const L = { brand_name: 'اسم البيزنس أو البراند', payment_methods: 'العملا بيدفعوا إزاي', shipping_cost: 'الشحن بيكلفك كام في الطلب (0 لو مفيش شحن)', daily_cap: 'أقصى صرف إعلانات في اليوم', monthly_cap: 'أقصى صرف إعلانات في الشهر' };
    const s2 = stepCard(2, 'البيزنس', st.business_done, false,
      h('div', { class: 'g2' },
        h('label', { class: 'fld' }, L.brand_name, bf.brand_name),
        h('label', { class: 'fld' }, 'إعلاناتك أغلبها بتجيب إيه', goalSel),
        tgtBox,
        ...['payment_methods', 'shipping_cost', 'daily_cap', 'monthly_cap'].map(k => h('label', { class: 'fld' }, L[k], bf[k]))),
      h('div', { class: 'small muted' }, 'مش لازم كل إعلاناتك تبقى بيع. لو عندك حملة رسايل وحملة فيديو، كل واحدة بتتقاس على هدفها هي. تحط هدف لكل نوع من «الإعدادات».'),
      h('div', { class: 'small muted' }, 'تقدر تغيّر أي حاجة من دول بعدين من «الإعدادات».'), h('div', { class: 'btns' }, bSave));

    // 3. Meta
    const mBtn = h('button', { class: 'btn pri', type: 'button' }, 'جرّب واحفظ');
    mBtn.addEventListener('click', busy(mBtn, async () => {
      const r = await api('/api/setup/meta', { method: 'POST', body: { token: val('m-token'), account_id: val('m-acct'), app_secret: val('m-secret') } });
      toast('متوصّل بحساب ' + (r.name || r.account)); await refreshStatus(); render();
    }));
    const mOff = h('button', { class: 'btn', type: 'button' }, 'افصل Meta');
    mOff.addEventListener('click', busy(mOff, async () => { await api('/api/setup/meta', { method: 'DELETE' }); render(); }));
    const s3 = stepCard(3, 'Meta Ads', st.meta.connected, false,
      st.meta.connected ? h('div', { class: 'banner info' }, 'متوصّل بحساب ' + st.meta.account + ' · الـtoken: ' + st.meta.token) : null,
      h('div', null, 'محتاجين حاجتين من Meta: **token** (زي مفتاح بيخلّي البرنامج يقرا إعلاناتك ويعدّل فيها بإذنك) ورقم حساب الإعلانات. ده بيتعمل مرة واحدة وبياخد حوالي 15 دقيقة.'.replace(/\*\*/g, '')),
      guide('الجزء الأول: اعمل App على Meta', [
        'افتح ' + 'developers.facebook.com/apps' + ' وسجّل دخول بنفس حساب فيسبوك اللي بتعمل بيه الإعلانات.',
        'دوس **Create app**.',
        'لما يسألك عايز تعمل إيه، اختار **Create & manage ads** (أو أي اختيار فيه Marketing API).',
        'اربط الـApp بالـ**Business portfolio** بتاع البراند لما يطلب منك، وكمّل لحد ما الـApp يتعمل.',
      ], !st.meta.connected, 'رابط: developers.facebook.com/apps'),
      guide('الجزء التاني: اعمل «System user» وادّيله صلاحيات', [
        'افتح ' + 'business.facebook.com/settings' + ' (Business settings).',
        'من القايمة اختار **Users** ← **System users** ← **Add**. اكتب اسم زي «Sales team» واختار الدور **Admin**.',
        'وانت على الـsystem user ده، دوس **Assign assets** ← **Ad accounts** ← اختار حساب الإعلانات ← فعّل **Manage campaigns** (أو Full control) ← **Assign**.',
        'من القايمة اختار **Apps** ← اختار الـApp اللي عملته ← **Assign people** ← ضيف الـsystem user بصلاحية **Full control**.',
      ], !st.meta.connected),
      guide('الجزء التالت: طلّع الـtoken', [
        'ارجع لـ**System users** واختار الـsystem user.',
        'دوس **Generate new token**.',
        'اختار الـApp بتاعك من القايمة.',
        'في مدة الـtoken اختار **Never** (مبيخلصش).',
        'علّم على الصلاحيات دي: **ads_management** و **ads_read** و **business_management**.',
        'دوس **Generate token**. لو Meta طلبت تأكيد، هتبعتلك كود على الإيميل.',
        'انسخ الـtoken كله (سطر طويل بيبدأ غالباً بـEAA) والزقه تحت. ماتبعتهوش لحد ولا تكتبه في أي شات.',
      ], !st.meta.connected),
      guide('رقم حساب الإعلانات', [
        'افتح **Ads Manager**.',
        'بص على العنوان فوق في المتصفح: هتلاقي **act=** وبعدها رقم. انسخ الرقم ده.',
        'أو من Business settings ← **Accounts** ← **Ad accounts**، الرقم مكتوب تحت اسم الحساب.',
      ], !st.meta.connected),
      guide('App secret (اختياري بس بيزوّد الأمان)', [
        'من developers.facebook.com/apps افتح الـApp بتاعك.',
        '**App settings** ← **Basic** ← جنب **App secret** دوس **Show** (هيطلب باسورد فيسبوك).',
        'انسخه والزقه في الخانة الأخيرة تحت.',
      ], false),
      h('div', { class: 'btns' }, link('https://developers.facebook.com/apps', 'افتح developers.facebook.com'), link('https://business.facebook.com/settings', 'افتح Business settings'), link('https://adsmanager.facebook.com/', 'افتح Ads Manager')),
      h('div', { class: 'small muted' }, 'واجهة Meta بتتغير كل شوية. لو اسم زرار مختلف شوية، دوّر على أقرب اسم ليه.'),
      h('div', { class: 'g2' }, secret('الـtoken', 'm-token', 'EAA…'), h('label', { class: 'fld' }, 'رقم حساب الإعلانات', h('input', { class: 'in ltr', id: 'm-acct', inputmode: 'numeric', placeholder: '1234567890', value: (st.meta.account || '').replace('act_', '') })),
        secret('App secret (اختياري)', 'm-secret')),
      h('div', { class: 'small muted' }, 'البرنامج بيجرّب الـtoken الأول. لو اشتغل بيتحفظ على اللابتوب ده بس، ومبيرجعش يظهر في الصفحة تاني.'),
      h('div', { class: 'btns' }, mBtn, st.meta.connected ? mOff : null));

    // 4. Telegram
    const tBtn = h('button', { class: 'btn pri', type: 'button' }, 'جرّب واحفظ');
    tBtn.addEventListener('click', busy(tBtn, async () => {
      const r = await api('/api/setup/telegram', { method: 'POST', body: { token: val('t-token') } });
      ui.botUser = r.username; toast('البوت @' + r.username + ' جاهز'); render();
    }));
    const tOff = h('button', { class: 'btn', type: 'button' }, 'افصل Telegram');
    tOff.addEventListener('click', busy(tOff, async () => { await api('/api/setup/telegram', { method: 'DELETE' }); render(); }));
    let tgWait = null;
    if (st.telegram.token && !st.telegram.chat) {
      const me = h('button', { class: 'btn pri', type: 'button', disabled: !st.telegram.candidate }, st.telegram.candidate ? 'ده أنا (' + st.telegram.candidate.name + ')' : 'مستني /start منك…');
      me.addEventListener('click', busy(me, async () => { await api('/api/setup/telegram-confirm', { method: 'POST', body: {} }); toast('تمام، بصّ على Telegram'); render(); }));
      const bot = st.telegram.bot || ui.botUser || '';
      tgWait = h('div', { class: 'banner warn' }, h('div', null, 'دلوقتي افتح البوت بتاعك في Telegram ودوس Start (أو ابعتله /start). أول ما يوصلنا الزرار ده هيتفعّل:'),
        bot && /^[A-Za-z0-9_]{5,64}$/.test(bot) ? link('https://t.me/' + bot, 'افتح @' + bot) : null, me);
      if (!st.telegram.candidate) tgPoll = setInterval(async () => { try { const x = await api('/api/setup'); if (x.telegram.candidate) render(); } catch (e) { /* keep waiting */ } }, 3000);
    }
    const tDone = st.telegram.token && st.telegram.chat;
    const s4 = stepCard(4, 'Telegram على موبايلك', tDone, true,
      h('div', null, 'عشان توصلك الملخصات والموافقات على الموبايل بزرار. ده ببلاش.'),
      tDone ? h('div', { class: 'banner info' }, 'متوصّل. جرّب تبعت /status للبوت.') : null,
      guide('اعمل البوت (3 دقايق)', [
        'افتح **Telegram** على موبايلك أو اللابتوب.',
        'دوّر على **@BotFather** (عليه علامة زرقا) وافتحه ودوس **Start**. أو افتح الرابط: t.me/BotFather',
        'ابعتله **/newbot**.',
        'هيسألك على اسم: اكتب أي اسم، زي «تيم المبيعات».',
        'هيسألك على username لازم يخلص بـ**bot**، زي nourwear_team_bot.',
        'هيبعتلك رسالة فيها سطر شكله زي **123456789:ABCdef…**. ده الـtoken: انسخه والزقه تحت.',
      ], !tDone),
      tDone ? null : h('div', { class: 'g2' }, secret('الـtoken بتاع البوت', 't-token', '123456789:ABC…')),
      tgWait, h('div', { class: 'btns' }, tDone ? null : tBtn, st.telegram.token ? tOff : null));

    // 5. Meta MCP (optional, advanced)
    const xBtn = h('button', { class: 'btn', type: 'button' }, 'جرّب واحفظ');
    xBtn.addEventListener('click', busy(xBtn, async () => { const r = await api('/api/setup/mcp', { method: 'POST', body: { token: val('x-token') } }); toast('متوصّل · ' + r.tools + ' أداة'); render(); }));
    const s5 = stepCard(5, 'أدوات Meta الإضافية (MCP)', st.mcp.token, true,
      h('div', { class: 'small muted' }, 'مش ضروري خالص للبداية. سيبه لحد ما الفريق يشتغل كويس.'),
      h('div', { class: 'btns' }, link('https://developers.facebook.com/apps', 'افتح Apps'), link('https://developers.facebook.com/tools/explorer/', 'افتح Graph API Explorer'), link('https://developers.facebook.com/tools/debug/accesstoken/', 'افتح Access Token Debugger')),
      guide('أولاً: جهّز الـApp (مرة واحدة)', [
        'افتح **developers.facebook.com/apps** وادخل على الـApp بتاعك.',
        'من **Use cases** دوس **Add** واختار **Create & manage ads with ads MCP server**. من غيرها صلاحية ads_mcp_management مش هتظهر، وMeta هترفض الـtoken.',
      ], false),
      guide('ثانياً: اعمل الـtoken', [
        'افتح **Graph API Explorer**، ومن فوق اختار الـApp بتاعك.',
        'من **User or Page** اختار **User Token** (مش Page).',
        'من **Permissions** ضيف: ads_mcp_management و ads_read و ads_management و business_management و pages_show_list و instagram_basic و catalog_management.',
        'دوس **Generate Access Token** ووافق على كل الصلاحيات.',
        'الـtoken ده بيعيش **ساعة بس**. انسخه، وافتح **Access Token Debugger**، والزقه ودوس **Debug** وبعدين **Extend Access Token** تحت. هيطلعلك token جديد بيعيش حوالي شهرين: ده اللي تلزقه هنا.',
        'لو رفضه، الصفحة هتقولك بالظبط إيه الناقص.',
      ], false),
      h('div', { class: 'g2' }, secret('الـtoken', 'x-token')), h('div', { class: 'btns' }, xBtn));

    const ready = st.meta.connected && st.business_done;
    const finish = h('section', { class: 'card' }, h('h2', null, ready ? 'كده جاهز' : 'لما تخلص الخطوات 1 و2 و3'),
      h('div', null, 'الفريق هيشتغل لوحده كل يوم في مواعيده طول ما البرنامج مفتوح. ووضع التجربة شغال: أي قرار بيتسجل بس من غير ما يغيّر حاجة في Meta، لحد ما تقفله بنفسك من «الإعدادات».'),
      h('div', { class: 'btns' }, h('a', { class: 'btn pri', href: '#overview' }, 'روح للصفحة الرئيسية'), h('a', { class: 'btn', href: '#customers' }, 'سجّل أول طلبات')));
    page('الإعداد أول مرة', 'خطوة خطوة. مش محتاج تفتح أي ملف.', null,
      st.demo_flag ? h('div', { class: 'banner warn' }, 'البرنامج شغال دلوقتي في وضع البيانات التجريبية. بعد ما توصّل Meta، اقفله وافتحه تاني من «تشغيل.command».') : null,
      s1, s2, s3, s4, s5, finish);
  }

  // ---------------------------------------------------------------- router
  const VIEWS = { overview: viewOverview, team: viewTeam, decisions: viewDecisions, profit: viewProfit, customers: viewCustomers, settings: viewSettings, setup: viewSetup };
  async function render() {
    const view = (location.hash || '#overview').slice(1);
    const fn = VIEWS[view] || viewOverview;
    current = VIEWS[view] ? view : 'overview';
    for (const a of document.querySelectorAll('.nav')) {
      const on = a.dataset.view === current;
      a.classList.toggle('on', on);
      if (on) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
    }
    try { await fn(); } catch (e) { main.replaceChildren(h('div', { class: 'banner bad' }, 'حصلت مشكلة: ' + e.message)); }
  }
  window.addEventListener('hashchange', () => { clearInterval(tgPoll); render(); main.focus(); });
  let wasRunning = false;
  setInterval(async () => {
    await refreshStatus();
    const running = !!(STATUS && STATUS.running.length);
    const typing = document.activeElement && ['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement.tagName);
    if ((running || wasRunning) && (current === 'overview' || current === 'team') && !typing) render();
    wasRunning = running;
  }, 8000);
  (async () => {
    await refreshStatus();
    try {
      const st = await api('/api/setup');
      const todo = !st.meta.connected && !st.demo_flag;
      document.getElementById('nb-setup').hidden = !todo;
      if (todo && !location.hash) location.hash = '#setup';
    } catch (e) { /* ignore */ }
    render();
  })();
})();
