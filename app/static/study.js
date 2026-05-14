(function () {
  const cards = Array.from(document.querySelectorAll(".study-card"));
  const done = document.querySelector(".study-done");
  const progressEl = document.getElementById("progress");
  if (cards.length === 0) return;

  let idx = 0;

  function show(i) {
    cards.forEach((c, j) => c.classList.toggle("hidden", i !== j));
    if (done) done.classList.toggle("hidden", true);
    if (progressEl) progressEl.textContent = i;
    // Reset reveal state for the newly-shown card.
    const cur = cards[i];
    if (!cur) return;
    const back = cur.querySelector(".study-back");
    const reveal = cur.querySelector(".reveal-row");
    const grades = cur.querySelector(".grade-buttons");
    if (back) back.classList.add("hidden");
    if (reveal) reveal.classList.remove("hidden");
    if (grades) grades.classList.add("hidden");
    // For shadowing, no front/back split — go straight to grading.
    if (cur.dataset.kind === "shadowing") {
      if (reveal) reveal.classList.add("hidden");
      if (grades) grades.classList.remove("hidden");
    }
  }

  function finish() {
    cards.forEach((c) => c.classList.add("hidden"));
    if (done) done.classList.remove("hidden");
    if (progressEl) progressEl.textContent = cards.length;
  }

  function next() {
    idx += 1;
    if (idx >= cards.length) finish();
    else show(idx);
  }

  async function grade(card, rating) {
    const cid = card.dataset.captureId;
    const cardId = card.dataset.cardId || "";
    // /grade/{capture_id}/{rest_of_card_id} — strip the "<capture_id>:" prefix
    const suffix = cardId.startsWith(cid + ":") ? cardId.slice(cid.length + 1) : cardId;
    const url = `/grade/${encodeURIComponent(cid)}/${suffix}?rating=${encodeURIComponent(rating)}`;
    try {
      const r = await fetch(url, { method: "POST" });
      if (!r.ok) console.warn("grade failed", r.status);
    } catch (e) {
      console.warn("grade error", e);
    }
    next();
  }

  function suffixFromCardId(captureId, cardId) {
    return cardId.startsWith(captureId + ":") ? cardId.slice(captureId.length + 1) : cardId;
  }

  async function deleteCard(card) {
    const cid = card.dataset.captureId;
    const cardId = card.dataset.cardId || "";
    if (!confirm("Delete this card? (other cards from this capture stay)")) return;
    const r = await fetch(`/api/card/${encodeURIComponent(cid)}/${suffixFromCardId(cid, cardId)}`, {
      method: "DELETE",
    });
    if (r.ok) {
      next();  // advance past the deleted card
    } else {
      alert("Delete failed");
    }
  }

  async function editCard(card) {
    const cid = card.dataset.captureId;
    const cardId = card.dataset.cardId || "";
    const kind = card.dataset.kind;
    const frontEl = card.querySelector(kind === "forward" ? ".prompt-en" : kind === "backward" ? ".prompt-it" : ".shadow-it");
    const backEl = card.querySelector(kind === "forward" ? ".answer-it" : kind === "backward" ? ".answer-en" : null);
    const currentFront = (frontEl && frontEl.textContent || "").trim();
    const currentBack = (backEl && backEl.textContent || "").trim();
    const newFront = prompt("Edit FRONT:", currentFront);
    if (newFront === null) return;
    const newBack = prompt("Edit BACK:", currentBack);
    if (newBack === null) return;
    const r = await fetch(`/api/card/${encodeURIComponent(cid)}/${suffixFromCardId(cid, cardId)}/edit`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ front: newFront, back: newBack }),
    });
    if (r.ok) {
      window.location.reload();
    } else {
      const body = await r.text().catch(() => "");
      alert("Edit failed: " + body);
    }
  }

  document.addEventListener("click", (e) => {
    const t = e.target;
    if (!(t instanceof HTMLElement)) return;
    if (t.classList.contains("card-delete")) {
      const card = t.closest(".study-card");
      if (card) deleteCard(card);
      return;
    }
    if (t.classList.contains("card-edit")) {
      const card = t.closest(".study-card");
      if (card) editCard(card);
      return;
    }
    if (t.classList.contains("rate")) {
      const rate = parseFloat(t.dataset.rate || "1");
      const card = t.closest(".study-card");
      const audio = card && card.querySelector("audio");
      if (audio instanceof HTMLAudioElement) {
        audio.playbackRate = rate;
        audio.play().catch(() => {});
        const buttons = card ? card.querySelectorAll(".rate") : [];
        buttons.forEach((b) => b.classList.toggle("active", b === t));
      }
      return;
    }
    if (t.classList.contains("reveal")) {
      const card = t.closest(".study-card");
      if (!card) return;
      const back = card.querySelector(".study-back");
      const reveal = card.querySelector(".reveal-row");
      const grades = card.querySelector(".grade-buttons");
      if (back) back.classList.remove("hidden");
      if (reveal) reveal.classList.add("hidden");
      if (grades) grades.classList.remove("hidden");
    } else if (t.classList.contains("grade")) {
      const card = t.closest(".study-card");
      if (!card) return;
      const rating = t.dataset.rating;
      if (rating) grade(card, rating);
    }
  });

  document.addEventListener("keydown", (e) => {
    const cur = cards[idx];
    if (!cur || cur.classList.contains("hidden")) return;
    const grades = cur.querySelector(".grade-buttons");
    const revealRow = cur.querySelector(".reveal-row");
    const isRevealed = grades && !grades.classList.contains("hidden");
    if (e.code === "Space") {
      e.preventDefault();
      if (!isRevealed && revealRow && !revealRow.classList.contains("hidden")) {
        const r = revealRow.querySelector(".reveal");
        if (r instanceof HTMLElement) r.click();
      }
      return;
    }
    if (!isRevealed) return;
    const map = { "1": "again", "2": "hard", "3": "good", "4": "easy" };
    const rating = map[e.key];
    if (rating) {
      e.preventDefault();
      grade(cur, rating);
    }
  });

  show(0);
})();
