/*
 * Conor O'Shea Fitness: cookie consent banner + GA4 funnel events.
 *
 * Loaded on every page of conorosheafitness.com AND on
 * lowbackassessment.conorosheafitness.com (from this same URL), so the
 * consent choice and click tracking stay identical across both sites.
 *
 * How it fits together:
 *  1. Each page's <head> sets Google Consent Mode v2 defaults to "denied"
 *     and loads gtag.js (needed there so Search Console can verify via GA).
 *  2. This file reads the stored choice from a cookie on
 *     .conorosheafitness.com (shared by the main site and the assessment).
 *     No choice yet: show the banner. Choice made: apply it silently.
 *  3. Clicks on main funnel CTAs and confirmed Aura bookings are sent to GA4.
 *
 * The consent cookie itself is strictly necessary (it remembers the
 * answer), so it is set without asking.
 */
(function () {
  'use strict';

  var CONSENT_COOKIE = 'cof_consent';
  var CONSENT_DAYS = 180;
  var ROOT_DOMAIN = 'conorosheafitness.com';

  window.dataLayer = window.dataLayer || [];
  if (typeof window.gtag !== 'function') {
    window.gtag = function () { window.dataLayer.push(arguments); };
  }
  var gtag = window.gtag;

  // ---------- Consent storage ----------
  function readConsent() {
    var m = document.cookie.match(new RegExp('(?:^|; )' + CONSENT_COOKIE + '=([^;]*)'));
    return m ? decodeURIComponent(m[1]) : null;
  }

  function writeConsent(value) {
    var host = location.hostname;
    var domainPart = (host === ROOT_DOMAIN || host.slice(-(ROOT_DOMAIN.length + 1)) === '.' + ROOT_DOMAIN)
      ? '; domain=.' + ROOT_DOMAIN
      : '';
    document.cookie = CONSENT_COOKIE + '=' + encodeURIComponent(value) +
      '; max-age=' + (CONSENT_DAYS * 86400) + '; path=/' + domainPart + '; SameSite=Lax' +
      (location.protocol === 'https:' ? '; Secure' : '');
  }

  // ---------- Apply a choice to Google + HubSpot ----------
  function applyConsent(value) {
    var granted = value === 'granted';
    gtag('consent', 'update', {
      analytics_storage: granted ? 'granted' : 'denied',
      // No ads run on the site; keep ad storage off either way.
      ad_storage: 'denied',
      ad_user_data: 'denied',
      ad_personalization: 'denied'
    });
    // HubSpot tracking follows the same choice.
    window._hsp = window._hsp || [];
    if (granted) {
      window._hsp.push(['doNotTrack', { track: true }]);
    } else {
      window._hsp.push(['doNotTrack']);
    }
  }

  // ---------- Banner ----------
  var STYLE =
    '#cof-consent{position:fixed;left:16px;right:16px;bottom:16px;z-index:2147483000;' +
    'max-width:560px;margin:0 auto;background:#1a1715;color:#f3efe8;' +
    'border:1px solid rgba(243,239,232,.14);border-radius:4px;padding:20px 22px;' +
    'box-shadow:0 12px 40px rgba(0,0,0,.45);font-family:Barlow,-apple-system,BlinkMacSystemFont,"Helvetica Neue",Arial,sans-serif;' +
    'font-size:15px;line-height:1.5}' +
    '#cof-consent p{margin:0 0 14px;color:#d8d2c8}' +
    '#cof-consent strong{color:#f3efe8;display:block;margin-bottom:4px;font-size:16px}' +
    '#cof-consent .cof-row{display:flex;gap:10px;flex-wrap:wrap}' +
    '#cof-consent button{flex:1 1 140px;cursor:pointer;font:inherit;font-weight:700;font-size:14px;' +
    'letter-spacing:.05em;text-transform:uppercase;padding:13px 18px;border-radius:2px;transition:background .2s,border-color .2s}' +
    '#cof-consent .cof-accept{background:#d1602e;color:#0f0e0d;border:1px solid #d1602e}' +
    '#cof-consent .cof-accept:hover{background:#e2703c;border-color:#e2703c}' +
    '#cof-consent .cof-decline{background:transparent;color:#f3efe8;border:1px solid rgba(243,239,232,.32)}' +
    '#cof-consent .cof-decline:hover{border-color:#f3efe8}' +
    '#cof-consent button:focus-visible{outline:2px solid #e2703c;outline-offset:2px}' +
    '.cof-consent-link{background:none;border:0;padding:0;color:inherit;font:inherit;cursor:pointer;text-decoration:underline}';

  function showBanner() {
    if (document.getElementById('cof-consent')) return;
    if (!document.getElementById('cof-consent-style')) {
      var s = document.createElement('style');
      s.id = 'cof-consent-style';
      s.textContent = STYLE;
      document.head.appendChild(s);
    }
    var box = document.createElement('div');
    box.id = 'cof-consent';
    box.setAttribute('role', 'dialog');
    box.setAttribute('aria-live', 'polite');
    box.setAttribute('aria-label', 'Cookie preferences');
    box.innerHTML =
      '<p><strong>Cookies</strong>We use analytics cookies to understand how people find and use this site, ' +
      'so we can make it more useful. Nothing is used for advertising.</p>' +
      '<div class="cof-row">' +
      '<button type="button" class="cof-decline">Decline</button>' +
      '<button type="button" class="cof-accept">Accept</button>' +
      '</div>';
    box.querySelector('.cof-accept').addEventListener('click', function () { choose('granted'); });
    box.querySelector('.cof-decline').addEventListener('click', function () { choose('denied'); });
    document.body.appendChild(box);
  }

  function hideBanner() {
    var box = document.getElementById('cof-consent');
    if (box) box.parentNode.removeChild(box);
  }

  function choose(value) {
    writeConsent(value);
    applyConsent(value);
    hideBanner();
    gtag('event', value === 'granted' ? 'consent_granted' : 'consent_denied');
  }

  // Lets a footer link ("Cookie settings") reopen the banner.
  window.cofOpenCookieSettings = showBanner;

  // ---------- Funnel click events ----------
  function linkLocation(a) {
    if (a.closest('nav, .nav')) return 'nav';
    if (a.closest('footer, .footer')) return 'footer';
    var section = a.closest('section[id], section[class], header, main');
    if (section) return section.id || (section.className || '').split(' ')[0] || section.tagName.toLowerCase();
    return 'body';
  }

  function classify(href) {
    var url;
    try { url = new URL(href, location.href); } catch (e) { return null; }
    if (url.hostname === 'lowbackassessment.' + ROOT_DOMAIN && location.hostname !== url.hostname) {
      return 'click_take_assessment';
    }
    var path = url.pathname;
    if (/\/discovery-call(\.html)?$/.test(path)) return 'click_book_discovery_call';
    if (/\/(assessment-call|assess-call)[\w-]*(\.html)?$/.test(path)) return 'click_book_assessment_call';
    return null;
  }

  document.addEventListener('click', function (e) {
    var a = e.target && e.target.closest ? e.target.closest('a[href]') : null;
    if (!a) return;
    var name = classify(a.getAttribute('href'));
    if (!name) return;
    gtag('event', name, {
      link_text: (a.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 100),
      link_url: a.href,
      link_location: linkLocation(a),
      transport_type: 'beacon'
    });
  }, true);

  // Aura's embed dispatches this host event only after a confirmed booking.
  // Do not forward booking details or attendee data to GA4.
  window.addEventListener('aura:scheduler:booking_succeeded', function (e) {
    if (!e.detail || e.detail.event !== 'booking_succeeded') return;
    gtag('event', 'consultation_booked');
  });

  // ---------- Start ----------
  function init() {
    var stored = readConsent();
    if (stored === 'granted' || stored === 'denied') {
      applyConsent(stored);
    } else {
      showBanner();
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
