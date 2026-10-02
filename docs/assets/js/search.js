/* InvoiceGuard docs — client-side search over assets/js/search-index.json. */
(function () {
  var input = document.getElementById("search-input");
  var box = document.getElementById("search-results");
  if (!input || !box) return;
  var index = null;

  function load(cb) {
    if (index) return cb(index);
    fetch("assets/js/search-index.json")
      .then(function (r) { return r.json(); })
      .then(function (data) { index = data; cb(index); })
      .catch(function () { index = []; cb(index); });
  }

  function esc(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function render(q, pages) {
    var ql = q.toLowerCase();
    var hits = pages
      .map(function (p) {
        var hay = (p.title + " " + p.description + " " + p.text).toLowerCase();
        var score = 0;
        ql.split(/\s+/).forEach(function (tok) {
          if (!tok) return;
          if (p.title.toLowerCase().indexOf(tok) !== -1) score += 3;
          else if (hay.indexOf(tok) !== -1) score += 1;
          else score -= 2;
        });
        return { p: p, score: score };
      })
      .filter(function (h) { return h.score > 0; })
      .sort(function (a, b) { return b.score - a.score; })
      .slice(0, 8);
    if (!hits.length) {
      box.innerHTML = '<div class="sr-empty">No matches in these docs.</div>';
    } else {
      box.innerHTML = hits
        .map(function (h) {
          return (
            '<a href="' + esc(h.p.url) + '"><div class="sr-title">' +
            esc(h.p.title) +
            '</div><div class="sr-desc">' +
            esc(h.p.description || "") +
            "</div></a>"
          );
        })
        .join("");
    }
    box.hidden = false;
  }

  input.addEventListener("input", function () {
    var q = input.value.trim();
    if (q.length < 2) { box.hidden = true; box.innerHTML = ""; return; }
    load(function (pages) { render(q, pages); });
  });
  input.addEventListener("keydown", function (e) {
    if (e.key === "Escape") { box.hidden = true; input.blur(); }
  });
  document.addEventListener("click", function (e) {
    if (!e.target.closest(".search-wrap")) box.hidden = true;
  });
})();
