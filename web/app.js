"use strict";
const $ = id => document.getElementById(id);
const examples = [
  { query: "工商银行2025年本集团营业收入和净利润分别是多少？", company: "工商银行" },
  { query: "建设银行2025年不良贷款率是多少？", company: "建设银行" },
  { query: "比较工商银行和建设银行2025年的核心一级资本充足率。", company: "" }
];
const qualityLabels = { sample_layout_checked: "已核对样本", page_text_unreviewed: "原页文本 · 未逐页复核", raw_grid_unreviewed: "原始表格 · 待核对", auto_layout_unreviewed: "自动恢复表格 · 待核对" };
let activeResult = null;
let busy = false;
let service = null;
let sky = null;
const escapeHTML = value => String(value ?? "").replace(/[&<>"']/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]));

async function api(path, options) {
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "服务请求失败，请稍后重试。");
  return data;
}

function inline(text, allowed) {
  return escapeHTML(text).replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/\[(E\d+)\]/g, (_, id) => allowed.has(id)
      ? `<button class="citation" type="button" data-evidence="${id}" aria-label="查看证据${id}">${id}</button>`
      : `<span class="citation invalid">${id}</span>`);
}

function markdown(text, evidence) {
  const allowed = new Set(evidence.map(row => row.evidence_id));
  const lines = String(text).split("\n");
  let result = "", paragraph = [];
  const flush = () => { if (paragraph.length) result += `<p>${paragraph.map(line => inline(line, allowed)).join("<br>")}</p>`; paragraph = []; };
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i].trim();
    if (!line) { flush(); continue; }
    if (line.startsWith("|") && /^\|?\s*:?-+/.test((lines[i + 1] || "").trim())) {
      flush();
      const cells = row => row.trim().replace(/^\||\|$/g, "").split("|").map(cell => cell.trim());
      result += `<table><thead><tr>${cells(line).map(cell => `<th>${inline(cell, allowed)}</th>`).join("")}</tr></thead><tbody>`;
      i += 2;
      for (; i < lines.length && lines[i].trim().startsWith("|"); i++) result += `<tr>${cells(lines[i]).map(cell => `<td>${inline(cell, allowed)}</td>`).join("")}</tr>`;
      i--; result += "</tbody></table>";
    } else if (/^#{1,6}\s/.test(line)) {
      flush(); result += `<h3>${inline(line.replace(/^#{1,6}\s+/, ""), allowed)}</h3>`;
    } else paragraph.push(line);
  }
  flush(); return result;
}

function reportLink(row) {
  const code = row.stock_code || String(row.chunk_id || "").slice(0, 6);
  return service?.report_codes.includes(code) ? `/reports/${code}.pdf#page=${Number(row.pdf_page_start)}` : safeSource(row.source_url);
}

function safeSource(url) {
  try { const parsed = new URL(url); return parsed.protocol === "https:" ? parsed.href : "#"; } catch { return "#"; }
}

function focusEvidence(id) {
  const card = document.getElementById(`evidence-${id}`);
  if (!card) return;
  card.open = true;
  document.querySelectorAll(".evidence-card.highlight").forEach(item => item.classList.remove("highlight"));
  card.classList.add("highlight");
  card.scrollIntoView({ behavior: "smooth", block: "center" });
  card.querySelector("summary").focus({ preventScroll: true });
}

function renderResult(result, historical = false) {
  activeResult = result;
  const evidence = result.evidence || [];
  const sources = result.sources || [];
  $("empty-state").hidden = true;
  $("answer-result").hidden = false;
  $("answered-question").textContent = result.query;
  $("answer-badge").textContent = historical ? "历史回答" : "本次回答";
  $("answer-content").innerHTML = markdown(result.answer || result.error || "未生成有效回答，请查看运行记录。", evidence);
  const notes = [];
  if (result.status !== "completed") notes.push(result.error || "本次未完整完成回答，请核对记录。");
  if (result.insufficient_evidence) notes.push("模型认为证据不足，未能完整回答。");
  if (result.citation_check && !result.citation_check.passed) notes.push("引用编号检查未通过，请查看原文核对。");
  $("answer-note").hidden = !notes.length;
  $("answer-note").textContent = notes.join(" ");
  $("answer-meta").textContent = `${historical ? "已保存记录" : "本次完成"} · ${result.elapsed_seconds ?? "—"} 秒 · ${result.retrieval?.mode === "hybrid" ? "混合检索" : result.retrieval?.mode === "bm25" ? "BM25检索" : "向量检索"}`;
  $("export-record").href = `/api/export?id=${encodeURIComponent(result.record_id)}`;
  $("sources-panel").hidden = !sources.length;
  $("citation-status").textContent = result.citation_check?.passed ? "引用编号有效 · 请核对内容" : "引用需核对";
  $("source-list").innerHTML = sources.map(row => `<div class="source-card"><div class="source-top"><button class="citation" data-evidence="${escapeHTML(row.evidence_id)}" type="button">${escapeHTML(row.evidence_id)}</button><strong>${escapeHTML(row.company)}</strong><span class="subtle">PDF第${Number(row.pdf_page_start)}页</span></div><p>${escapeHTML(row.section)}</p><div class="source-links"><button class="citation" type="button" data-evidence="${escapeHTML(row.evidence_id)}">查看原文</button><a href="${escapeHTML(reportLink(row))}" target="_blank" rel="noopener">原报告 ↗</a><a href="${escapeHTML(safeSource(row.source_url))}" target="_blank" rel="noopener">下载来源 ↗</a></div></div>`).join("");
  $("evidence-panel").hidden = !evidence.length;
  $("evidence-count").textContent = `· ${evidence.length} 块`;
  const used = new Set(sources.map(row => row.evidence_id));
  $("evidence-list").innerHTML = evidence.map(row => {
    const scores = row.retrieval_scores || {};
    const scoreText = Object.entries(scores).map(([key, score]) => `${key === "vector" ? "向量" : "BM25"} ${Number(score).toFixed(3)}`).join(" · ");
    return `<details class="evidence-card" id="evidence-${escapeHTML(row.evidence_id)}"><summary><div class="evidence-top"><strong><span class="evidence-number">${escapeHTML(row.evidence_id)}</span>${escapeHTML(row.company)} · PDF ${Number(row.pdf_page_start)}</strong>${used.has(row.evidence_id) ? '<span class="used-tag">已引用</span>' : ''}</div><p class="evidence-section">${escapeHTML(row.section)}</p><p class="excerpt">${escapeHTML(row.content)}</p><div class="evidence-toggle"><span>${escapeHTML(qualityLabels[row.quality_status] || "待核对")}</span><span>展开原文 +</span></div></summary><div class="evidence-body"><pre>${escapeHTML(row.content)}</pre><div class="evidence-meta"><p>块ID：${escapeHTML(row.chunk_id)}</p><p>${escapeHTML(scoreText)}</p><p><a href="${escapeHTML(reportLink(row))}" target="_blank" rel="noopener">打开原报告第${Number(row.pdf_page_start)}页 ↗</a></p></div></div></details>`;
  }).join("");
  $("expand-all").textContent = "展开全部";
  sky?.setEvidence(evidence, result.retrieval?.corpus_sha256);
}

async function loadHistory() {
  const data = await api("/api/history");
  const list = $("history-list"); list.replaceChildren();
  if (!data.records.length) { list.innerHTML = '<p class="hint">完成提问后，记录会保存到这里。</p>'; return; }
  data.records.forEach(row => {
    const button = document.createElement("button");
    button.type = "button"; button.className = "history-item";
    const label = document.createElement("span"); label.textContent = row.query;
    const time = document.createElement("small"); time.textContent = new Date(row.created_at).toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false });
    button.append(label, time); button.title = row.query;
    button.addEventListener("click", async () => {
      if (busy) return;
      try {
        const result = await api(`/api/record?id=${encodeURIComponent(row.record_id)}`);
        renderResult(result, true); $("question").value = result.query;
        $("company").value = result.retrieval?.filters?.company || "";
        $("mode").value = result.retrieval?.mode || "hybrid";
        const savedK = String(result.retrieval?.top_k || 6);
        if ([...$("top-k").options].some(option => option.value === savedK)) $("top-k").value = savedK;
        $("request-status").className = "request-status";
        $("request-status").textContent = "正在查看历史回答；点击生成回答可重新提问。";
      }
      catch (error) { showError(error.message); }
    });
    list.append(button);
  });
}

function showError(message) { $("request-status").textContent = message; $("request-status").className = "request-status error"; }

$("question-form").addEventListener("submit", async event => {
  event.preventDefault(); if (busy) return;
  const query = $("question").value.trim(); if (!query) { showError("请先填写问题。"); return; }
  busy = true;
  $("ask-button").disabled = true; $("ask-button").textContent = "正在生成…";
  $("request-status").className = "request-status busy";
  $("request-status").textContent = "正在检索年报原文并生成回答，请稍候。";
  $("empty-state").classList.add("loading");
  try {
    const job = await api("/api/jobs", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ query, company: $("company").value, mode: $("mode").value, top_k: Number($("top-k").value) }) });
    let state;
    do { await new Promise(resolve => setTimeout(resolve, 650)); state = await api(`/api/jobs/${job.job_id}`); } while (state.status === "running");
    if (state.status === "failed") throw new Error(state.message);
    renderResult(state.result);
    if (state.result.status === "completed") { $("request-status").className = "request-status"; $("request-status").textContent = "回答已保存。点击回答中的证据编号查看依据。"; }
    else showError(state.result.error || "本次回答未完整完成，已保留运行记录。");
    await loadHistory();
  } catch (error) { showError(error.message || "连接中断，请检查本地服务。"); }
  finally { busy = false; $("ask-button").disabled = !(service?.answer_ready); $("ask-button").innerHTML = '生成回答 <span aria-hidden="true">↗</span>'; $("empty-state").classList.remove("loading"); }
});

document.addEventListener("click", event => {
  const button = event.target.closest("[data-evidence]");
  if (button) focusEvidence(button.dataset.evidence);
  const example = event.target.closest("[data-example]");
  if (example && !busy) { const item = examples[Number(example.dataset.example)]; $("question").value = item.query; $("company").value = item.company; $("question").focus(); }
});
$("expand-all").addEventListener("click", () => { const cards = [...document.querySelectorAll(".evidence-card")]; const expand = cards.some(card => !card.open); cards.forEach(card => card.open = expand); $("expand-all").textContent = expand ? "收起全部" : "展开全部"; });

(async () => {
  $("ask-button").disabled = true;
  try {
    service = await api("/api/status");
    $("company").replaceChildren();
    const all = document.createElement("option"); all.value = ""; all.textContent = `全部 ${service.companies.length} 家银行`; $("company").append(all);
    service.companies.forEach(company => { const option = document.createElement("option"); option.value = company; option.textContent = company; $("company").append(option); });
    $("corpus-description").textContent = `${service.companies.length}家银行 · ${service.report_year}年度报告`;
    $("corpus-scope").textContent = `课程作业 · ${service.companies.length}家银行 · 年报全文`;
    $("chunk-count").textContent = service.chunk_count.toLocaleString("zh-CN");
    $("service-status").textContent = service.answer_ready ? "索引与回答模型就绪" : "索引就绪 · 回答模型未配置";
    $("service-status").classList.add("ready");
    $("ask-button").disabled = !service.answer_ready;
    if (!service.answer_ready) showError("请在本地 .env 中配置回答模型密钥后重试。");
    await loadHistory();
    sky = new CourseSky({api, reportLink, qualityLabels, focusEvidence});
    sky.load().then(() => { if (activeResult) sky.setEvidence(activeResult.evidence || [], activeResult.retrieval?.corpus_sha256); });
  } catch (error) { $("service-status").textContent = "服务连接失败"; showError(error.message); }
})();
