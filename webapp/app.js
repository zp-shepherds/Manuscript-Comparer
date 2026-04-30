const bookSelect = document.getElementById("bookSelect");
const manuscriptASelect = document.getElementById("manuscriptA");
const manuscriptBSelect = document.getElementById("manuscriptB");
const manuscriptStage = document.getElementById("manuscriptStage");
const actions = document.getElementById("actions");
const compareBtn = document.getElementById("compareBtn");
const toggleBtn = document.getElementById("toggleBtn");
const summaryPanel = document.getElementById("summaryPanel");
const resultsPanel = document.getElementById("resultsPanel");
const resultsBody = document.getElementById("resultsBody");
const legend = document.getElementById("legend");
const messageEl = document.getElementById("message");

const totalWordsAEl = document.getElementById("totalWordsA");
const totalWordsBEl = document.getElementById("totalWordsB");
const matchingWordsEl = document.getElementById("matchingWords");
const differentWordsEl = document.getElementById("differentWords");
const wordAgreementEl = document.getElementById("wordAgreement");
const totalVersesEl = document.getElementById("totalVerses");
const matchingVersesEl = document.getElementById("matchingVerses");
const differentVersesEl = document.getElementById("differentVerses");
const verseAgreementEl = document.getElementById("verseAgreement");

let comparisonRows = [];
let differencesOnly = false;

function escapeHtml(text) {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

async function fetchJson(url) {
  const response = await fetch(url);
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.error || "Request failed.");
  }
  return payload;
}

function setSelectOptions(select, options, placeholder) {
  select.innerHTML = "";
  const placeholderOption = document.createElement("option");
  placeholderOption.value = "";
  placeholderOption.textContent = placeholder;
  select.appendChild(placeholderOption);

  for (const option of options) {
    const element = document.createElement("option");
    element.value = option.id || option.code;
    element.textContent = option.name
      ? `${option.name} (${option.manuscriptCount})`
      : `${option.label} — ${option.filename}`;
    select.appendChild(element);
  }
}

function resetStats() {
  totalWordsAEl.textContent = "0";
  totalWordsBEl.textContent = "0";
  matchingWordsEl.textContent = "0";
  differentWordsEl.textContent = "0";
  wordAgreementEl.textContent = "0%";
  totalVersesEl.textContent = "0";
  matchingVersesEl.textContent = "0";
  differentVersesEl.textContent = "0";
  verseAgreementEl.textContent = "0%";
}

function resetResults() {
  comparisonRows = [];
  resultsBody.innerHTML = "";
  summaryPanel.classList.add("hidden");
  resultsPanel.classList.add("hidden");
  legend.classList.add("hidden");
  toggleBtn.disabled = true;
  toggleBtn.textContent = "Show Differences Only";
  differencesOnly = false;
  resetStats();
}

function renderTokenHtml(token, cls) {
  return `<span class="${cls}">${escapeHtml(token)}</span>`;
}

function renderRowTexts(row) {
  if (!row.hasTextA && !row.hasTextB) {
    return {
      a: '<span class="placeholder">(no line)</span>',
      b: '<span class="placeholder">(no line)</span>',
    };
  }

  if (!row.hasTextA && row.hasTextB) {
    return {
      a: '<span class="placeholder">(no line)</span>',
      b: `<span class="tok-add">${escapeHtml(row.textB)}</span>`,
    };
  }

  if (row.hasTextA && !row.hasTextB) {
    return {
      a: `<span class="tok-del">${escapeHtml(row.textA)}</span>`,
      b: '<span class="placeholder">(no line)</span>',
    };
  }

  if (!row.isDiff) {
    return {
      a: escapeHtml(row.textA),
      b: escapeHtml(row.textB),
    };
  }

  const outA = [];
  const outB = [];
  for (const op of row.ops) {
    if (op.type === "eq") {
      outA.push(escapeHtml(op.token));
      outB.push(escapeHtml(op.token));
    } else if (op.type === "del") {
      outA.push(renderTokenHtml(op.token, "tok-del"));
    } else {
      outB.push(renderTokenHtml(op.token, "tok-add"));
    }
  }

  return {
    a: outA.join(" ") || `<span class="tok-del">${escapeHtml(row.textA)}</span>`,
    b: outB.join(" ") || `<span class="tok-add">${escapeHtml(row.textB)}</span>`,
  };
}

function renderRows() {
  resultsBody.innerHTML = "";
  const visibleRows = differencesOnly
    ? comparisonRows.filter((row) => row.isDiff)
    : comparisonRows;

  for (const row of visibleRows) {
    const tr = document.createElement("tr");
    if (row.isDiff) {
      tr.classList.add("diff");
    }

    const rendered = renderRowTexts(row);
    tr.innerHTML = `
      <td>
        <div class="unit-label">${escapeHtml(row.label)}</div>
        <div class="unit-index">Row ${row.line}</div>
      </td>
      <td>
        <div class="row-match">
          <span class="percent">${row.wordAgreement === null ? "n/a" : `${row.wordAgreement}%`}</span>
          <span class="note">${row.wordAgreement === null ? "no text in either file" : `${row.sharedWords}/${row.comparisonSize} words match`}</span>
        </div>
      </td>
      <td>${rendered.a}</td>
      <td>${rendered.b}</td>
    `;
    resultsBody.appendChild(tr);
  }

  if (visibleRows.length === 0) {
    const tr = document.createElement("tr");
    tr.innerHTML = '<td colspan="4" class="placeholder">No rows to display in this view.</td>';
    resultsBody.appendChild(tr);
  }
}

function updateStats(stats) {
  totalWordsAEl.textContent = String(stats.totalWordsA);
  totalWordsBEl.textContent = String(stats.totalWordsB);
  matchingWordsEl.textContent = String(stats.matchingWords);
  differentWordsEl.textContent = String(stats.differentWords);
  wordAgreementEl.textContent = `${stats.wordAgreement}%`;
  totalVersesEl.textContent = String(stats.totalVerses);
  matchingVersesEl.textContent = String(stats.matchingVerses);
  differentVersesEl.textContent = String(stats.differentVerses);
  verseAgreementEl.textContent = `${stats.verseAgreement}%`;
}

async function loadBooks() {
  const payload = await fetchJson("/api/books");
  setSelectOptions(bookSelect, payload.books, "Choose a book...");
}

async function loadManuscripts(book) {
  const payload = await fetchJson(`/api/manuscripts?book=${encodeURIComponent(book)}`);
  setSelectOptions(manuscriptASelect, payload.manuscripts, "Choose the first manuscript...");
  setSelectOptions(manuscriptBSelect, payload.manuscripts, "Choose the second manuscript...");
  manuscriptStage.classList.remove("hidden");
  actions.classList.remove("hidden");
}

async function compareSelectedManuscripts() {
  const book = bookSelect.value;
  const manuscriptA = manuscriptASelect.value;
  const manuscriptB = manuscriptBSelect.value;

  if (!book || !manuscriptA || !manuscriptB) {
    messageEl.textContent = "Choose a book and both manuscripts before comparing.";
    return;
  }

  try {
    const payload = await fetchJson(
      `/api/compare?book=${encodeURIComponent(book)}&a=${encodeURIComponent(manuscriptA)}&b=${encodeURIComponent(manuscriptB)}`
    );
    comparisonRows = payload.rows;
    differencesOnly = false;
    toggleBtn.disabled = false;
    toggleBtn.textContent = "Show Differences Only";
    summaryPanel.classList.remove("hidden");
    resultsPanel.classList.remove("hidden");
    legend.classList.remove("hidden");
    updateStats(payload.stats);
    renderRows();
    messageEl.textContent = `Comparing ${payload.bookName}: ${payload.manuscriptA.id} and ${payload.manuscriptB.id}`;
  } catch (error) {
    resetResults();
    messageEl.textContent = error.message;
  }
}

bookSelect.addEventListener("change", async () => {
  resetResults();
  manuscriptStage.classList.add("hidden");
  actions.classList.add("hidden");
  messageEl.textContent = "";

  if (!bookSelect.value) {
    return;
  }

  try {
    await loadManuscripts(bookSelect.value);
    messageEl.textContent = "Choose the two manuscripts you want to compare.";
  } catch (error) {
    messageEl.textContent = error.message;
  }
});

compareBtn.addEventListener("click", compareSelectedManuscripts);

toggleBtn.addEventListener("click", () => {
  differencesOnly = !differencesOnly;
  toggleBtn.textContent = differencesOnly ? "Show All Lines" : "Show Differences Only";
  renderRows();
});

loadBooks().catch((error) => {
  messageEl.textContent = error.message;
});
