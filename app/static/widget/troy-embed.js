/*
 * Troy Embed — embeddable resolution widget for any SaaS website.
 *
 * Zero external dependencies (vanilla JS). Drop this script on any page:
 *
 *   <script src="https://your-troy-host/widget/troy-embed.js"
 *           data-endpoint="https://your-troy-host/api/v1/saas/resolve"
 *           data-product-name="CloudScribe"></script>
 *
 * It renders a floating "Resolve with Troy" button that opens a modern
 * resolution drawer with ticket input, live state feedback, and a
 * structured resolution report. Configuration can also be supplied via
 * window.TROY_EMBED_CONFIG or TroyEmbed.init({...}).
 */
(function (global) {
  'use strict';

  if (global.TroyEmbed && global.TroyEmbed._installed) return;

  function currentScript() {
    if (document.currentScript) return document.currentScript;
    var scripts = document.getElementsByTagName('script');
    for (var i = scripts.length - 1; i >= 0; i--) {
      var src = scripts[i].src || '';
      if (src.indexOf('troy-embed') !== -1) return scripts[i];
    }
    return null;
  }

  function deriveEndpoint(script) {
    var origin = '';
    if (script && script.src) {
      try {
        var url = new URL(script.src, global.location.href);
        origin = url.origin;
      } catch (e) { /* fall through to relative endpoint */ }
    }
    return origin + '/api/v1/saas/resolve';
  }

  function readConfig() {
    var script = currentScript();
    var cfg = global.TROY_EMBED_CONFIG || {};
    function pick(name, fallback) {
      if (cfg[name]) return cfg[name];
      if (script && script.getAttribute('data-' + name)) return script.getAttribute('data-' + name);
      return fallback;
    }
    return {
      endpoint: pick('endpoint', pick('api-url', deriveEndpoint(script))),
      productName: pick('product-name', pick('productName', 'Troy Support')),
      title: pick('title', 'Resolve with Troy'),
      placeholder: pick('placeholder', 'Describe your issue — e.g. "I was charged twice for order ORD-88219"…'),
      buttonLabel: pick('button-label', 'Get resolution')
    };
  }

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  function init(userConfig) {
    var config = Object.assign({}, readConfig(), userConfig || {});
    if (document.getElementById('troy-embed-root')) {
      return { open: openDrawer, close: closeDrawer, config: config };
    }

    var root = el('div');
    root.id = 'troy-embed-root';

    var styles = el('style');
    styles.textContent = [
      '#troy-embed-root{font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif}',
      '.troy-fab{position:fixed;right:20px;bottom:20px;z-index:2147483647;border:none;border-radius:999px;padding:12px 18px;background:#4f46e5;color:#fff;font-size:15px;font-weight:600;box-shadow:0 8px 24px rgba(0,0,0,.25);cursor:pointer}',
      '.troy-fab:hover{background:#4338ca}',
      '.troy-drawer{position:fixed;right:20px;bottom:80px;z-index:2147483647;width:min(400px,calc(100vw - 40px));max-height:min(70vh,640px);display:none;flex-direction:column;background:#fff;color:#111827;border-radius:16px;box-shadow:0 16px 48px rgba(0,0,0,.3);overflow:hidden}',
      '.troy-drawer.open{display:flex}',
      '.troy-head{display:flex;align-items:center;justify-content:space-between;padding:14px 16px;background:#4f46e5;color:#fff}',
      '.troy-head h2{margin:0;font-size:16px}',
      '.troy-close{background:none;border:none;color:#fff;font-size:20px;cursor:pointer;line-height:1}',
      '.troy-body{padding:16px;overflow:auto;display:flex;flex-direction:column;gap:10px}',
      '.troy-body label{font-size:13px;font-weight:600}',
      '.troy-body textarea,.troy-body input{width:100%;box-sizing:border-box;border:1px solid #d1d5db;border-radius:10px;padding:10px;font:inherit;font-size:14px}',
      '.troy-body textarea{min-height:90px;resize:vertical}',
      '.troy-row{display:flex;gap:8px}',
      '.troy-row>*{flex:1}',
      '.troy-submit{border:none;border-radius:10px;padding:11px;background:#4f46e5;color:#fff;font-weight:600;cursor:pointer}',
      '.troy-submit:disabled{opacity:.6;cursor:wait}',
      '.troy-state{font-size:13px;color:#6b7280;min-height:18px}',
      '.troy-state.error{color:#b91c1c}',
      '.troy-state.ok{color:#047857}',
      '.troy-report{display:none;border:1px solid #e5e7eb;border-radius:12px;padding:12px;background:#f9fafb;font-size:14px;white-space:pre-wrap;word-break:break-word}',
      '.troy-report.show{display:block}',
      '.troy-meta{display:flex;gap:6px;flex-wrap:wrap}'
    ].join('\n');
    root.appendChild(styles);

    var fab = el('button', 'troy-fab', config.title);
    fab.type = 'button';
    fab.setAttribute('aria-haspopup', 'dialog');
    fab.setAttribute('aria-expanded', 'false');

    var drawer = el('section', 'troy-drawer');
    drawer.setAttribute('role', 'dialog');
    drawer.setAttribute('aria-label', config.title);
    drawer.setAttribute('aria-hidden', 'true');

    var head = el('div', 'troy-head');
    head.appendChild(el('h2', '', config.title + ' — ' + config.productName));
    var closeBtn = el('button', 'troy-close', '×');
    closeBtn.type = 'button';
    closeBtn.setAttribute('aria-label', 'Close');
    head.appendChild(closeBtn);

    var body = el('div', 'troy-body');
    var ticketLabel = el('label', '', 'Your ticket');
    var ticket = el('textarea');
    ticket.id = 'troy-embed-ticket';
    ticket.placeholder = config.placeholder;
    ticketLabel.setAttribute('for', ticket.id);

    var row = el('div', 'troy-row');
    var customerId = el('input');
    customerId.placeholder = 'Customer ID (optional)';
    customerId.setAttribute('aria-label', 'Customer ID');
    var orderId = el('input');
    orderId.placeholder = 'Order ID (optional)';
    orderId.setAttribute('aria-label', 'Order ID');
    row.appendChild(customerId);
    row.appendChild(orderId);

    var state = el('div', 'troy-state', 'Idle');
    state.setAttribute('role', 'status');
    var submit = el('button', 'troy-submit', config.buttonLabel);
    submit.type = 'button';
    var report = el('div', 'troy-report');

    body.appendChild(ticketLabel);
    body.appendChild(ticket);
    body.appendChild(row);
    body.appendChild(submit);
    body.appendChild(state);
    body.appendChild(report);
    drawer.appendChild(head);
    drawer.appendChild(body);
    root.appendChild(fab);
    root.appendChild(drawer);
    document.body.appendChild(root);

    function setState(text, kind) {
      state.textContent = text;
      state.className = 'troy-state' + (kind ? ' ' + kind : '');
    }

    function renderReport(data) {
      report.textContent = '';
      report.classList.add('show');
      var meta = el('div', 'troy-meta');
      ['status', 'category', 'refund_id'].forEach(function (key) {
        if (data && data[key]) meta.appendChild(el('span', '', key + ': ' + data[key]));
      });
      if (meta.children.length) report.appendChild(meta);
      var text = (data && (data.customer_communication || data.message || data.response || data.summary)) || 'Resolution received.';
      var comm = el('p', '', text);
      report.appendChild(comm);
      if (data && data.resolution_report && data.resolution_report.summary) {
        report.appendChild(el('p', '', data.resolution_report.summary));
      }
      if (data && data.action_result) {
        report.appendChild(el('p', '', 'Action: ' + (data.action_result.status || data.action_status || 'completed')));
      }
    }

    function openDrawer() {
      drawer.classList.add('open');
      drawer.setAttribute('aria-hidden', 'false');
      fab.setAttribute('aria-expanded', 'true');
      ticket.focus();
    }

    function closeDrawer() {
      drawer.classList.remove('open');
      drawer.setAttribute('aria-hidden', 'true');
      fab.setAttribute('aria-expanded', 'false');
      fab.focus();
    }

    fab.addEventListener('click', function () {
      if (drawer.classList.contains('open')) closeDrawer(); else openDrawer();
    });
    closeBtn.addEventListener('click', closeDrawer);
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && drawer.classList.contains('open')) closeDrawer();
    });

    submit.addEventListener('click', function () {
      var complaint = ticket.value.trim();
      if (!complaint) {
        setState('Please describe your issue first.', 'error');
        ticket.focus();
        return;
      }
      submit.disabled = true;
      setState('Troy is working on your ticket…');
      report.classList.remove('show');

      fetch(config.endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
        body: JSON.stringify({
          complaint: complaint,
          customer_id: customerId.value.trim() || undefined,
          order_id: orderId.value.trim() || undefined,
          auto_execute: true,
          dry_run: false
        })
      }).then(function (response) {
        return response.text().then(function (text) {
          var data = {};
          try { data = JSON.parse(text); } catch (e) { data = { message: text }; }
          if (!response.ok || (data && data.ok === false)) {
            throw new Error((data && data.error) || ('Request failed (' + response.status + ')'));
          }
          return data;
        });
      }).then(function (data) {
        setState('Resolution ready.', 'ok');
        renderReport(data);
      }).catch(function (err) {
        setState('Could not reach Troy: ' + (err && err.message ? err.message : err), 'error');
      }).then(function () {
        submit.disabled = false;
      });
    });

    if (global.TroyEmbed) { global.TroyEmbed.open = openDrawer; global.TroyEmbed.close = closeDrawer; }
    return { open: openDrawer, close: closeDrawer, config: config };
  }

  var api = { init: init, open: null, close: null, config: readConfig(), _installed: true };
  global.TroyEmbed = api;

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () { init(); });
  } else {
    init();
  }
})(typeof window !== 'undefined' ? window : this);
