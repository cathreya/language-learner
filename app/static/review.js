(function () {
  const popup = document.getElementById("popup");
  if (!popup) return;
  const wordEl = popup.querySelector(".popup-word");
  const glossEl = popup.querySelector(".popup-gloss");
  const lemmaEl = popup.querySelector(".popup-lemma");
  const posEl = popup.querySelector(".popup-pos");
  let active = null;

  function show(tok) {
    if (active) active.classList.remove("active");
    active = tok;
    tok.classList.add("active");

    wordEl.textContent = tok.textContent;
    glossEl.textContent = tok.dataset.gloss || "";
    lemmaEl.textContent = tok.dataset.lemma || "";
    posEl.textContent = tok.dataset.pos || "";

    const r = tok.getBoundingClientRect();
    popup.classList.remove("hidden");
    popup.setAttribute("aria-hidden", "false");

    // Position below the word, clamped to viewport
    const pw = popup.offsetWidth;
    const ph = popup.offsetHeight;
    let left = r.left + window.scrollX + r.width / 2 - pw / 2;
    left = Math.max(8, Math.min(left, window.innerWidth - pw - 8));
    let top = r.bottom + window.scrollY + 8;
    if (top + ph > window.scrollY + window.innerHeight - 8) {
      top = r.top + window.scrollY - ph - 8;
    }
    popup.style.left = left + "px";
    popup.style.top = top + "px";
  }

  function hide() {
    popup.classList.add("hidden");
    popup.setAttribute("aria-hidden", "true");
    if (active) {
      active.classList.remove("active");
      active = null;
    }
  }

  document.addEventListener("click", (e) => {
    const t = e.target;
    if (t && t.classList && t.classList.contains("tok")) {
      e.stopPropagation();
      if (active === t) {
        hide();
      } else {
        show(t);
      }
    } else if (!popup.contains(t)) {
      hide();
    }
  });

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") hide();
  });
})();
