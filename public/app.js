/**
 * Freedom Broker — Conversation Quality Analytics (CQA) Frontend Controller
 * Pure Vanilla JavaScript (ES6+), structured for standalone and Dynamics 365 Web Resource embedding.
 */
const App = {
  state: {
    activeView: "workspace",
    activeChecklist: null,
    editingChecklist: null,
    presetTemplates: [],
    llmConfig: {},
    stats: {},
    conversations: [],
    currentConversationId: null,
    currentConversation: null,
    quickFilter: "",
    openInlineAppealCritId: null,
    openInlineReviewCritId: null,
  },

  async init() {
    if (window.D365Bridge && window.D365Bridge.shellPreviewActive) {
      document.body.classList.add("d365-mode");
    }
    await this.loadGlobalState();
    await this.loadConversationsList();

    const initialId =
      (window.D365Bridge && window.D365Bridge.getInitialConversationId()) ||
      (this.state.conversations[0] && this.state.conversations[0].conversation_id) ||
      "p1-ru-001";

    if (initialId) {
      await this.selectConversation(initialId);
    }
  },

  showToast(message, kind = "") {
    const container = document.getElementById("toastContainer");
    if (!container) return;
    const el = document.createElement("div");
    el.className = `toast ${kind ? `toast-${kind}` : ""}`;
    el.textContent = message;
    container.appendChild(el);
    setTimeout(() => {
      el.style.opacity = "0";
      el.style.transition = "opacity 0.3s";
      setTimeout(() => el.remove(), 300);
    }, kind === "error" ? 8000 : 3600);
  },

  escapeHtml(str) {
    if (str === null || str === undefined) return "";
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  },

  async loadGlobalState() {
    const res = await fetch("/api/state");
    const data = await res.json();
    if (!data.isSuccess) return;

    this.state.activeChecklist = data.active_checklist;
    this.state.editingChecklist = JSON.parse(JSON.stringify(data.active_checklist));
    this.state.presetTemplates = data.preset_criteria_templates || [];
    this.state.llmConfig = data.llm_config || {};
    this.state.stats = data.stats || {};

    // Update header badges
    document.getElementById("navTotalBadge").textContent =
      this.state.stats.total_conversations || 0;
    const pendingCnt = this.state.stats.pending_appeals_count || 0;
    const resolvedCnt = this.state.stats.resolved_appeals_count || 0;
    const appBadge = document.getElementById("navAppealsBadge");
    appBadge.textContent = pendingCnt > 0 ? `${pendingCnt} новых` : `${resolvedCnt}`;
    appBadge.className = pendingCnt > 0 ? "nav-badge danger" : "nav-badge";

    document.getElementById("activeChecklistBanner").textContent =
      `Активный чек-лист: ${this.state.activeChecklist.name} (${this.state.activeChecklist.criteria.length} крит.)` +
      ` · Движок: ${this.state.llmConfig.llm_active ? this.state.llmConfig.model : "встроенный"}`;

    // Resume progress tracking of a background LLM batch (e.g. after page reload)
    if (data.llm_job && data.llm_job.status === "running" && this.state.trackedJobId !== data.llm_job.id) {
      this.trackJob(data.llm_job);
    }

    // Populate group filter dropdown
    const groupSelect = document.getElementById("groupFilter");
    if (groupSelect && this.state.stats.dataset_groups) {
      const currentVal = groupSelect.value;
      groupSelect.innerHTML = `<option value="">Все группы</option>` +
        this.state.stats.dataset_groups
          .map((g) => `<option value="${this.escapeHtml(g)}">${this.escapeHtml(g)}</option>`)
          .join("");
      groupSelect.value = currentVal;
    }
  },

  async loadConversationsList() {
    const search = document.getElementById("searchInput")?.value || "";
    const lang = document.getElementById("langFilter")?.value || "";
    const channel = document.getElementById("channelFilter")?.value || "";
    const group = document.getElementById("groupFilter")?.value || "";
    const filter = this.state.quickFilter || "";

    const params = new URLSearchParams({
      search,
      language: lang,
      channel,
      group_id: group,
      filter,
    });

    const res = await fetch(`/api/conversations?${params.toString()}`);
    const data = await res.json();
    if (!data.isSuccess) return;

    this.state.conversations = data.records || [];
    document.getElementById("listCountLabel").textContent = this.state.conversations.length;
    this.renderConversationsList();
  },

  applyFilters() {
    this.loadConversationsList();
  },

  setQuickFilter(filterVal) {
    this.state.quickFilter = filterVal;
    document.querySelectorAll("#quickFilterPills .filter-pill").forEach((btn) => {
      btn.classList.toggle("active", btn.getAttribute("data-filter") === filterVal);
    });
    this.loadConversationsList();
  },

  renderConversationsList() {
    const container = document.getElementById("convListContainer");
    if (!container) return;

    if (this.state.conversations.length === 0) {
      container.innerHTML = `<div style="padding:24px; text-align:center; color:var(--text-muted);">Диалоги по выбранному фильтру не найдены.</div>`;
      return;
    }

    container.innerHTML = this.state.conversations
      .map((item) => {
        const isActive = item.conversation_id === this.state.currentConversationId;
        const score = item.final_score;
        let scoreClass = "score-high";
        if (score !== null && score < 55) scoreClass = "score-low";
        else if (score !== null && score < 85) scoreClass = "score-mid";

        const scoreText = score !== null && score !== undefined ? `${Number(score).toFixed(2)}` : "N/A";
        const isVoice = item.channel === "voice call" || item.has_audio;
        const scoreChanged =
          item.initial_score !== null &&
          item.final_score !== null &&
          Math.abs(Number(item.initial_score) - Number(item.final_score)) > 0.001;

        return `
          <div class="conv-item ${isActive ? "active" : ""}" onclick="App.selectConversation('${this.escapeHtml(item.conversation_id)}')">
            <div class="conv-item-top">
              <div class="conv-id">
                <span>${isVoice ? "📞" : "💬"}</span>
                <span>${this.escapeHtml(item.conversation_id)}</span>
                <span class="mini-badge">${this.escapeHtml(item.language)}</span>
              </div>
              <div class="conv-score-pill ${scoreClass}">
                ${scoreChanged ? `<span style="text-decoration:line-through; opacity:0.65; font-weight:500; margin-right:3px;">${Number(item.initial_score).toFixed(0)}</span>` : ""}
                ${scoreText}
              </div>
            </div>
            <div class="conv-preview">${this.escapeHtml(item.client_preview || "Диалог с клиентом")}</div>
            <div class="conv-badges">
              ${isVoice ? `<span class="mini-badge voice">🔊 Аудиозапись</span>` : ""}
              ${item.has_critical_error ? `<span class="mini-badge crit">🚨 Грубая ошибка</span>` : ""}
              ${item.na_count > 0 ? `<span class="mini-badge na">N/A: ${item.na_count} (перераспред. +${item.na_weight_redistributed} б.)</span>` : ""}
              ${item.recommend_supervisor_audit ? `<span class="mini-badge appeal-pending" title="Байесовский движок Beta(α,β) рекомендует выборочный аудит супервизором">🎯 Аудит супервизора</span>` : ""}
              ${item.has_pending_appeal ? `<span class="mini-badge appeal-pending">⚖️ Апелляция на рассмотр.</span>` : ""}
              ${!item.has_pending_appeal && item.has_resolved_appeal ? `<span class="mini-badge appeal-done">✅ Пересмотрено QA</span>` : ""}
            </div>
          </div>
        `;
      })
      .join("");
  },

  async selectConversation(conversationId) {
    this.state.currentConversationId = conversationId;
    this.state.openInlineAppealCritId = null;
    this.state.openInlineReviewCritId = null;
    this.renderConversationsList();

    const res = await fetch(`/api/conversations/${encodeURIComponent(conversationId)}`);
    const data = await res.json();
    if (!data.isSuccess) {
      this.showToast(data.error || "Ошибка загрузки диалога");
      return;
    }

    this.state.currentConversation = data.conversation;
    const d365Label = document.getElementById("d365ContextEntity");
    if (d365Label) {
      d365Label.textContent = `Entity: msdyn_ocliveworkitem | Record: ${conversationId} (${data.conversation.language})`;
    }
    if (window.D365Bridge) {
      window.D365Bridge.notifyParentD365("conversationSelected", {
        conversation_id: conversationId,
        final_score: data.conversation.current_evaluation?.final_score,
      });
    }

    this.renderTranscriptPane();
    this.renderEvaluationPane();
  },

  // ==========================================================================
  // CENTER PANE: Transcript & Audio Player
  // ==========================================================================
  renderTranscriptPane() {
    const conv = this.state.currentConversation;
    if (!conv) return;

    const isVoice = conv.channel === "voice call" || Boolean(conv.audio_url);
    const waitSec = conv.current_evaluation?.meta?.first_response_wait_sec;
    const waitBadge =
      waitSec !== null && waitSec !== undefined
        ? `<span class="mini-badge" style="margin-left:6px;">⏱️ 1-й ответ оператора: ${Math.round(waitSec)} сек.</span>`
        : "";

    document.getElementById("transcriptHeaderTitle").innerHTML = `
      <span>${isVoice ? "📞 Звонок" : "💬 Чат"} <strong>${this.escapeHtml(conv.conversation_id)}</strong></span>
      <span class="mini-badge">${this.escapeHtml(conv.language)}</span>
      <span class="mini-badge">${this.escapeHtml(conv.dataset_group_id)}</span>
      ${waitBadge}
    `;

    // Configure audio player bar for voice calls
    const audioBar = document.getElementById("audioPlayerBar");
    const audioEl = document.getElementById("callAudioElement");
    const extLink = document.getElementById("externalAudioLink");

    if (isVoice && conv.audio_url) {
      audioBar.classList.remove("hidden");
      const targetSrc = conv.proxy_audio_url || conv.audio_url;
      if (audioEl.getAttribute("data-cid") !== conv.conversation_id) {
        audioEl.setAttribute("data-cid", conv.conversation_id);
        audioEl.src = targetSrc;
      }
      extLink.href = conv.audio_url;
    } else {
      audioBar.classList.add("hidden");
      audioEl.pause();
      audioEl.removeAttribute("src");
      audioEl.setAttribute("data-cid", "");
    }

    // Build reverse citation map: turn_index -> list of criterion codes referencing it
    const turnCitations = {};
    const critResults = conv.current_evaluation?.criteria_results || [];
    critResults.forEach((cr) => {
      (cr.evidence || []).forEach((ev) => {
        const idx = ev.turn_index;
        if (!turnCitations[idx]) turnCitations[idx] = [];
        turnCitations[idx].push({
          code: cr.code,
          name: cr.name,
          answer: cr.answer,
        });
      });
    });

    const roleLabels = {
      client: "Клиент (Client)",
      operator: "Оператор (Operator)",
      bot: "AIM AI-Бот (Контекст)",
      system: "Системное событие (System)",
    };

    const transcriptEl = document.getElementById("transcriptContainer");
    transcriptEl.innerHTML = (conv.transcript || [])
      .map((t) => {
        const idx = t.turn_index;
        const sp = (t.speaker || "system").toLowerCase();
        const roleLabel = roleLabels[sp] || sp;
        const cites = turnCitations[idx] || [];
        const offsetSec = t.offset_seconds || 0;
        const timeDisplay =
          isVoice && t.timestamp && t.timestamp.length > 10
            ? `${t.formatted_offset} (${t.timestamp.slice(11, 19)})`
            : t.timestamp;
        const sm = t.sphinx_meta;
        const sphinxBadge = sm
          ? `<span class="mini-badge" style="background:#eef2ff; color:#3730a3; border:1px solid #c7d2fe; font-size:10.5px;" title="Акустический сегмент CMU PocketSphinx VAD">🎙️ Sphinx VAD #${sm.segment_index}: ${sm.vad_start_sec ?? sm.start_sec}–${sm.vad_end_sec ?? sm.end_sec}с (${sm.rms_dbfs} dBFS)</span>`
          : "";
        const kzRuFixBadge = t.original_raw_text
          ? `<span class="mini-badge" style="background:#ecfdf5; color:#065f46; border:1px solid #a7f3d0; font-size:10.5px;" title="Исходный ASR (ошибка KZ→RU): ${this.escapeHtml(t.original_raw_text)}">✨ Исправлено KZ↔RU</span>`
          : "";

        return `
          <div class="turn-row speaker-${this.escapeHtml(sp)}" id="turn-${idx}">
            <div class="turn-meta">
              <span class="speaker-tag">[${idx}] ${this.escapeHtml(roleLabel)}</span>
              <div style="display:flex; gap:6px; align-items:center;">
                ${kzRuFixBadge}
                ${sphinxBadge}
                <button class="turn-time-btn" onclick="App.jumpToTurn(${idx}, ${offsetSec})" title="${isVoice ? "Перемотать аудиозапись к этой реплике" : "Ссылка на реплику"}">
                  ${isVoice ? "🔊 " : "🕒 "}${this.escapeHtml(timeDisplay)}
                </button>
              </div>
            </div>
            <div class="turn-text">${this.escapeHtml(t.text)}</div>
            ${
              cites.length > 0
                ? `<div class="turn-citations">
                    ${cites
                      .map(
                        (c) =>
                          `<span class="turn-cite-badge" title="Используется как доказательство в критерии ${this.escapeHtml(c.code)} ${this.escapeHtml(c.name)}">📌 Крит. ${this.escapeHtml(c.code)}: ${this.escapeHtml(c.answer)}</span>`
                      )
                      .join("")}
                   </div>`
                : ""
            }
          </div>
        `;
      })
      .join("");
  },

  jumpToTurn(turnIndex, offsetSeconds) {
    const row = document.getElementById(`turn-${turnIndex}`);
    if (row) {
      document.querySelectorAll(".turn-row.highlighted-turn").forEach((el) =>
        el.classList.remove("highlighted-turn")
      );
      row.scrollIntoView({ behavior: "smooth", block: "center" });
      row.classList.add("highlighted-turn");
      setTimeout(() => row.classList.remove("highlighted-turn"), 3500);
    }

    // If voice call has audio, seek and play
    const conv = this.state.currentConversation;
    const audioEl = document.getElementById("callAudioElement");
    if (conv && conv.audio_url && audioEl && offsetSeconds !== undefined) {
      try {
        audioEl.currentTime = Math.max(0, Number(offsetSeconds));
        audioEl.play().catch(() => {
          this.showToast(
            `Переход к отметке ${Math.floor(offsetSeconds / 60)}:${String(Math.floor(offsetSeconds % 60)).padStart(2, "0")}. Нажмите Play в плеере.`
          );
        });
      } catch (_) {}
    }
  },

  setAudioSpeed(speed) {
    const audioEl = document.getElementById("callAudioElement");
    if (audioEl) {
      audioEl.playbackRate = parseFloat(speed) || 1.0;
    }
  },

  async runPocketSphinxOnCurrentCall(mode = "enhance", audioBase64 = null) {
    const conv = this.state.currentConversation;
    if (!conv) return;
    const reportBox = document.getElementById("sphinxReportBox");
    if (reportBox) {
      reportBox.classList.remove("hidden");
      reportBox.innerHTML = `⏳ <strong>CMU PocketSphinx ASR:</strong> выполняется конвертация в 16 kHz mono PCM, VAD-сегментация и акустическое декодирование...`;
    }
    try {
      const res = await fetch("/api/asr/transcribe", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          conversation_id: conv.conversation_id,
          mode,
          apply_to_conversation: true,
          audio_base64: audioBase64,
        }),
      });
      const data = await res.json();
      if (!data.isSuccess) {
        throw new Error(data.error || "Ошибка распознавания");
      }
      this.showToast(data.message || "CMU PocketSphinx успешно обработал аудиозапись");
      await this.selectConversation(conv.conversation_id);
      await this.loadGlobalState();

      const updatedBox = document.getElementById("sphinxReportBox");
      if (updatedBox && data.sphinx_report) {
        const sr = data.sphinx_report;
        const corrHtml =
          (data.corrections || []).length > 0
            ? `<div style="margin-top:6px; padding-top:6px; border-top:1px dashed rgba(165,180,252,0.35);">
                <strong>✨ Исправлено билингвальных искажений (KZ/RU): ${data.corrections.length}</strong>
                ${data.corrections
                  .map(
                    (c) =>
                      `<div style="margin-top:3px;">Реплика [${c.turn_index}]: <span style="text-decoration:line-through; opacity:0.75;">${this.escapeHtml(c.before)}</span> → <strong>${this.escapeHtml(c.after)}</strong></div>`
                  )
                  .join("")}
               </div>`
            : "";
        updatedBox.classList.remove("hidden");
        updatedBox.innerHTML = `
          <div style="display:flex; justify-content:space-between; align-items:center; gap:8px; flex-wrap:wrap;">
            <div>
              <strong>🎙️ ${this.escapeHtml(sr.engine)} v${this.escapeHtml(sr.version)}</strong> ·
              Модель: <code>${this.escapeHtml(sr.model)}</code> ·
              Частота: <strong>${sr.sample_rate} Гц</strong> ·
              Длительность: <strong>${sr.audio_duration_sec} сек.</strong> ·
              Сегментов VAD: <strong>${sr.vad_segments_count}</strong> (${sr.total_speech_sec} сек. речи)
            </div>
            <div style="display:flex; gap:6px;">
              <button class="btn btn-sm" style="font-size:10.5px; padding:1px 6px; background:#1e1b4b; color:#c7d2fe; border:1px solid #6366f1;" onclick="App.runPocketSphinxOnCurrentCall('enhance')">Гибридный VAD+Текст</button>
              <button class="btn btn-sm" style="font-size:10.5px; padding:1px 6px; background:#1e1b4b; color:#c7d2fe; border:1px solid #6366f1;" onclick="App.runPocketSphinxOnCurrentCall('pure_sphinx')">Чистый Sphinx</button>
              <button class="btn btn-sm" style="font-size:10.5px; padding:1px 6px; background:transparent; color:#a5b4fc; border:none;" onclick="document.getElementById('sphinxReportBox').classList.add('hidden')">✕</button>
            </div>
          </div>
          ${corrHtml}
        `;
      }
    } catch (err) {
      if (reportBox) {
        reportBox.innerHTML = `❌ Ошибка CMU PocketSphinx: ${this.escapeHtml(err.message)}`;
      }
      this.showToast(`Ошибка PocketSphinx: ${err.message}`, "error");
    }
  },

  uploadAudioForPocketSphinx(event) {
    const file = event.target.files && event.target.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      const dataUrl = String(reader.result || "");
      const base64 = dataUrl.includes(",") ? dataUrl.split(",", 2)[1] : dataUrl;
      this.runPocketSphinxOnCurrentCall("pure_sphinx", base64);
    };
    reader.readAsDataURL(file);
    event.target.value = "";
  },

  // ==========================================================================
  // RIGHT PANE: Evaluation Scorecard, N/A Redistribution & Appeals (Контур 1 & 2)
  // ==========================================================================
  renderEvaluationPane() {
    const conv = this.state.currentConversation;
    const container = document.getElementById("evalCardContainer");
    if (!conv || !container) return;

    const initEv = conv.initial_evaluation || {};
    const currEv = conv.current_evaluation || {};
    const initScore =
      initEv.final_score !== null && initEv.final_score !== undefined
        ? `${Number(initEv.final_score).toFixed(2)}`
        : "Не рассчит.";
    const currScore =
      currEv.final_score !== null && currEv.final_score !== undefined
        ? `${Number(currEv.final_score).toFixed(2)}`
        : "Не рассчит.";

    const hasCrit = Boolean(currEv.has_critical_error);
    const critErrors = currEv.critical_errors || [];
    const naCount = currEv.na_count || 0;
    const naWeight = currEv.na_weight_redistributed || 0;
    const appWeight = currEv.applicable_weight_sum || 0;
    const rawEarned = currEv.raw_earned || 0;

    const sectionsHtml = (currEv.sections || [])
      .map((s) => {
        const pct =
          s.section_percent !== null && s.section_percent !== undefined
            ? `${Number(s.section_percent).toFixed(1)}%`
            : "N/A";
        return `
          <div class="section-bar-item">
            <div>
              <strong>${this.escapeHtml(s.section)}</strong>
              ${s.na_count > 0 ? `<span class="mini-badge na" style="margin-left:4px;">N/A: ${s.na_count}</span>` : ""}
              ${s.has_critical_error ? `<span class="mini-badge crit" style="margin-left:4px;">🚨 Нарушение</span>` : ""}
            </div>
            <div style="font-weight:700; color:var(--fb-dark);">
              ${s.applicable_weight > 0 ? `${s.raw_points} / ${s.applicable_weight} б. (${pct})` : s.has_critical_error ? "Флаг ДА" : "Без баллов / N/A"}
            </div>
          </div>
        `;
      })
      .join("");

    const criteriaCardsHtml = (currEv.criteria_results || [])
      .map((cr) => this.renderSingleCriterionCard(cr))
      .join("");

    const alg = currEv.algorithms || {};
    const det = alg.deterministic_math || {};
    const act = alg.actuarial_risk || {};
    const bb = act.bayesian_beta || {};
    const nlp = alg.nlp_structured || {};

    const riskTierColor =
      act.risk_tier === "CRITICAL"
        ? "var(--danger)"
        : act.risk_tier === "HIGH"
        ? "#b45309"
        : act.risk_tier === "MODERATE"
        ? "#1d4ed8"
        : "#047857";

    container.innerHTML = `
      <!-- Top Summary Score Card -->
      <div class="score-summary-card">
        <div class="score-comparison-row">
          <div class="score-box">
            <span class="score-box-label">Исходная оценка ИИ</span>
            <div class="score-box-value">${initScore} <span style="font-size:13px; font-weight:600; color:var(--text-muted);">/ 100</span></div>
            <span class="score-box-sub">Сырые баллы: ${initEv.raw_earned ?? 0} из ${initEv.applicable_weight_sum ?? 100} примен. веса</span>
          </div>
          <div class="score-box final-highlight">
            <span class="score-box-label">Финальный результат</span>
            <div class="score-box-value">${currScore} <span style="font-size:13px; font-weight:600; color:var(--text-muted);">/ 100</span></div>
            <span class="score-box-sub">W_active = ${appWeight}: (${rawEarned} / ${appWeight}) × 100 = <strong>${currScore}</strong></span>
          </div>
        </div>

        <!-- Evaluation engine (built-in vs live LLM) and API fallback warning -->
        <div class="engine-line ${currEv.meta?.llm_used ? "llm" : ""}">
          ${currEv.meta?.llm_used
            ? `🤖 Оценено моделью <strong>${this.escapeHtml(currEv.meta.model_name)}</strong> · ${currEv.meta.estimated_input_tokens ?? "?"} вх. / ${currEv.meta.estimated_output_tokens ?? "?"} вых. токенов · ≈ $${Number(currEv.meta.estimated_cost_usd || 0).toFixed(5)}`
            : `⚙️ Оценено встроенным гибридным движком`}
        </div>
        ${currEv.meta?.llm_fallback_note ? `<div class="llm-fallback-banner">⚠️ ${this.escapeHtml(currEv.meta.llm_fallback_note)}</div>` : ""}

        <!-- Critical Violation Banner (Criterion 08: Critical Fail Override) -->
        <div class="critical-banner ${hasCrit ? "has-error" : "no-error"}">
          <span style="font-size:16px;">${hasCrit ? "🚨" : "🛡️"}</span>
          <div>
            <div>
              <strong>Critical Fail Override (Критерий 08): ${hasCrit ? "ВЫЯВЛЕНО (ДА) — БЛОКИРОВКА ДОПУСКА" : "Не выявлено (НЕТ) — ДОПУСК ПОДТВЕРЖДЁН"}</strong>
            </div>
            ${
              hasCrit && critErrors[0]
                ? `<div style="font-weight:400; font-size:12px; margin-top:2px;">${this.escapeHtml(critErrors[0].reasoning)}</div>`
                : `<div style="font-weight:400; font-size:11.5px;">Булева маска не ломает шкалу весов W_active, но контролирует допуск оператора к прохождению аудита.</div>`
            }
          </div>
        </div>

        <!-- Proportional N/A Weight Redistribution Info (Dynamic Weight Rebalancing) -->
        ${
          naCount > 0
            ? `<div class="redistribution-banner">
                <strong>➗ Dynamic Weight Rebalancing при N/A (${naCount} крит. на сумму ${naWeight} б.):</strong><br/>
                Неприменимые критерии исключены из базы. Активный вес <strong>W_active = ${appWeight} б.</strong> пропорционально масштабирован к <strong>100.00 баллам</strong> (множитель ×${appWeight > 0 ? (100 / appWeight).toFixed(3) : "1"}).
               </div>`
            : ""
        }

        <!-- 4-Class Hybrid Algorithmic Telemetry Panel -->
        <div style="background:#f8fafc; border:1px solid #cbd5e1; border-radius:8px; padding:10px 12px; display:flex; flex-direction:column; gap:8px;">
          <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:6px;">
            <span style="font-size:11px; font-weight:800; text-transform:uppercase; color:#0f172a;">
              🧮 Актуарная, вероятностная и NLP-телеметрия диалога
            </span>
            <span class="mini-badge" style="background:${bb.recommend_supervisor_audit ? "#fef3c7" : "#dcfce7"}; color:${bb.recommend_supervisor_audit ? "#92400e" : "#166534"}; border:1px solid ${bb.recommend_supervisor_audit ? "#f59e0b" : "#86efac"};">
              ${bb.recommend_supervisor_audit ? "🎯 Рекомендован выборочный аудит супервизором" : "✅ Автоматический допуск (Beta-достоверность)"}
            </span>
          </div>

          <div style="display:grid; grid-template-columns: repeat(2, 1fr); gap:8px; font-size:11.5px;">
            <div style="background:#fff; border:1px solid #e2e8f0; border-radius:6px; padding:7px 9px;">
              <div style="color:var(--text-secondary); font-size:10.5px; font-weight:700;">1 · TIME-DELTA SLA & WEIBULL VaR(0.95)</div>
              <div style="margin-top:2px;">
                <strong>Δt_first:</strong> ${det.delta_t_first_sec ?? 0} с ${det.sla_c06_cap_applied ? `<span style="color:var(--danger); font-weight:700;">(&gt;180с: Крит.06 ≤ 7)</span>` : `<span style="color:#047857;">(≤180с SLA OK)</span>`}
              </div>
              <div style="margin-top:1px; color:var(--text-secondary);">
                <strong>VaR₀.₉₅:</strong> <strong>${act.var_95_sec ?? 0} с</strong> (Weibull k=${act.weibull_shape_k ?? 1.5}, λ=${act.weibull_scale_lambda ?? 0})
              </div>
            </div>

            <div style="background:#fff; border:1px solid #e2e8f0; border-radius:6px; padding:7px 9px;">
              <div style="color:var(--text-secondary); font-size:10.5px; font-weight:700;">2 · COLLECTIVE RISK MODEL</div>
              <div style="margin-top:2px;">
                <strong>Risk Index:</strong> <span style="font-weight:800; color:${riskTierColor};">${act.collective_risk_index_norm ?? 0} б.</span> (сырой: ${act.collective_risk_index_raw ?? 0})
              </div>
              <div style="margin-top:1px; color:var(--text-secondary);">
                Статус: <strong style="color:${riskTierColor};">${this.escapeHtml(act.risk_label || "Низкий риск")}</strong> (откл.: ${act.deviation_events_count ?? 0})
              </div>
            </div>

            <div style="background:#fff; border:1px solid #e2e8f0; border-radius:6px; padding:7px 9px;">
              <div style="color:var(--text-secondary); font-size:10.5px; font-weight:700;">3 · BAYESIAN CONFIDENCE Beta(α, β)</div>
              <div style="margin-top:2px;">
                <strong>Beta(${bb.alpha ?? 8.5}, ${bb.beta ?? 1.2}):</strong> E[p] = <strong>${bb.posterior_confidence ?? 92}%</strong>
              </div>
              <div style="margin-top:1px; color:var(--text-secondary);">
                Дисперсия Var = <code>${bb.posterior_variance ?? 0.005}</code> (порог ${bb.variance_threshold ?? 0.0085})
              </div>
            </div>

            <div style="background:#fff; border:1px solid #e2e8f0; border-radius:6px; padding:7px 9px;">
              <div style="color:var(--text-secondary); font-size:10.5px; font-weight:700;">4 · CONSTRAINED JSON FSA & DOM EVIDENCE</div>
              <div style="margin-top:2px;">
                <strong>Грамматика FSA:</strong> S₀₄∈{0..20,N/A}, S₀₆∈{0,4,7,10}
              </div>
              <div style="margin-top:1px; color:var(--text-secondary);">
                Привязок <code>evidence_line_index</code>: <strong>${nlp.targeted_evidence_citations_count ?? 0}</strong>${nlp.acoustic_vad_segments_linked ? ` · VAD: <strong>${nlp.acoustic_vad_segments_linked}</strong>` : ""}
              </div>
            </div>
          </div>
        </div>

        <!-- Section Subtotals -->
        <div>
          <div style="font-size:11px; font-weight:700; text-transform:uppercase; color:var(--text-secondary); margin-bottom:5px;">
            Результаты по секциям чек-листа
          </div>
          <div class="sections-grid">
            ${sectionsHtml}
          </div>
        </div>
      </div>

      <!-- Individual Criteria Cards -->
      ${criteriaCardsHtml}
    `;
  },

  renderSingleCriterionCard(cr) {
    const cid = cr.criterion_id;
    const ctype = cr.type;
    const ans = cr.answer;
    const ansStr = String(ans).toUpperCase();

    let badgeClass = "ans-yes";
    if (ansStr === "N/A") badgeClass = "ans-na";
    else if (ctype === "critical_flag") {
      badgeClass = ansStr === "ДА" ? "ans-no" : "ans-yes";
    } else if (ctype === "yes_no") {
      badgeClass = ansStr === "ДА" ? "ans-yes" : "ans-no";
    } else if (ctype === "scale") {
      const val = Number(ans);
      const maxS = Number(cr.max_scale || 20);
      if (val >= maxS) badgeClass = "ans-yes";
      else if (val <= 0) badgeClass = "ans-no";
      else badgeClass = "ans-partial";
    }

    const ansDisplay =
      ctype === "scale" && ansStr !== "N/A"
        ? `${ans} / ${cr.max_scale} (шкала)`
        : `${ans}`;

    const weightInfo =
      ctype === "critical_flag"
        ? "Без баллов (Флаг)"
        : cr.is_na
        ? `Вес ${cr.weight} → N/A (исключён)`
        : `Баллы: ${Number(cr.raw_points).toFixed(1)}/${cr.weight} → с перераспред.: ${Number(cr.redistributed_points).toFixed(2)} / ${Number(cr.effective_weight).toFixed(2)}`;

    const evidenceHtml = (cr.evidence || [])
      .map((ev) => {
        const offset = ev.offset_seconds || 0;
        const lineIdx = ev.evidence_line_index ?? ev.turn_index ?? 0;
        const quoteTxt = ev.evidence_quote ?? ev.text ?? "";
        return `
          <button class="evidence-btn" onclick="App.jumpToTurn(${lineIdx}, ${offset})">
            <div class="evidence-btn-header">
              <span>🔗 Индекс <code>evidence_line_index=${lineIdx}</code> (DOM <code>#turn-${lineIdx}</code>) · ${this.escapeHtml(ev.timestamp)} (${this.escapeHtml(ev.speaker)})</span>
              <span>Перейти к реплике ↗</span>
            </div>
            <div>«${this.escapeHtml(quoteTxt)}»</div>
            ${ev.note ? `<div style="font-size:11px; color:var(--text-secondary); margin-top:2px;">Пояснение: ${this.escapeHtml(ev.note)}</div>` : ""}
          </button>
        `;
      })
      .join("");

    const appeal = cr.appeal;
    const initSnap = cr.initial_evaluation;

    // Build options for reviewer changing the score
    let reviewOptionsHtml = "";
    if (ctype === "yes_no") {
      reviewOptionsHtml = `
        <option value="ДА" ${ans === "ДА" ? "selected" : ""}>ДА (полный вес ${cr.weight} б.)</option>
        <option value="НЕТ" ${ans === "НЕТ" ? "selected" : ""}>НЕТ (0 б.)</option>
        ${cr.allow_na ? `<option value="N/A" ${ans === "N/A" ? "selected" : ""}>N/A (Неприменимо)</option>` : ""}
      `;
    } else if (ctype === "critical_flag") {
      reviewOptionsHtml = `
        <option value="НЕТ" ${ans === "НЕТ" ? "selected" : ""}>НЕТ (Нарушений не выявлено)</option>
        <option value="ДА" ${ans === "ДА" ? "selected" : ""}>ДА (Грубая ошибка выявлена)</option>
      `;
    } else if (ctype === "scale") {
      reviewOptionsHtml = (cr.scale || [])
        .map(
          (st) =>
            `<option value="${st.value}" ${Number(ans) === Number(st.value) ? "selected" : ""}>Ступень ${st.value}: ${this.escapeHtml(st.label)}</option>`
        )
        .join("");
      if (cr.allow_na) {
        reviewOptionsHtml += `<option value="N/A" ${ans === "N/A" ? "selected" : ""}>N/A (Неприменимо)</option>`;
      }
    }

    const isViolationCard = ctype === "critical_flag" && ansStr === "ДА";
    const cardStateClass = isViolationCard
      ? "crit-violation"
      : appeal
      ? appeal.status === "pending"
        ? "has-appeal"
        : "appeal-resolved"
      : "";

    return `
      <div class="criterion-card ${cardStateClass}" id="crit-card-${this.escapeHtml(cid)}">
        <div class="crit-top">
          <div class="crit-title-group">
            <span class="crit-section-tag">${this.escapeHtml(cr.code)} · ${this.escapeHtml(cr.section)}</span>
            <span class="crit-name">${this.escapeHtml(cr.name)}</span>
          </div>
          <div class="crit-badges-right">
            <span class="weight-pill">${weightInfo}</span>
            <span class="ans-badge ${badgeClass}">${this.escapeHtml(ansDisplay)}</span>
          </div>
        </div>

        <div style="font-size:11.5px; color:var(--text-secondary);">
          Правило: ${this.escapeHtml(cr.description)}
        </div>

        <div class="crit-reasoning">
          <strong>Обоснование:</strong> ${this.escapeHtml(cr.reasoning)}
        </div>

        ${
          cr.is_na && cr.na_reason
            ? `<div class="crit-na-box"><strong>Условие неприменимости (N/A):</strong> ${this.escapeHtml(cr.na_reason)}</div>`
            : ""
        }

        ${
          !cr.is_na && cr.missing_action_location
            ? `<div class="crit-missing-loc"><strong>📍 Проверенный участок (где ожидалось действие):</strong> ${this.escapeHtml(cr.missing_action_location)}</div>`
            : ""
        }

        ${
          evidenceHtml
            ? `<div class="evidence-list">
                <div style="font-size:11px; font-weight:700; color:var(--text-secondary);">Доказательства в транскрипте (нажмите для перехода):</div>
                ${evidenceHtml}
               </div>`
            : ""
        }

        <!-- Appeal & Reviewer Audit Trail if present -->
        ${
          appeal
            ? `<div class="appeal-box ${appeal.status !== "pending" ? "resolved" : ""}">
                <div style="display:flex; justify-content:space-between; align-items:center;">
                  <strong>⚖️ Апелляция (${appeal.status === "pending" ? "Ожидает решения проверяющего" : appeal.status === "changed" ? "Оценка изменена проверяющим" : "Исходная оценка подтверждена"})</strong>
                  <span class="mini-badge">${this.escapeHtml(appeal.author || "Оператор")}</span>
                </div>
                <div><strong>Спорная (исходная) оценка:</strong> <code>${this.escapeHtml(appeal.original_answer)}</code> (Итог до апелляции: ${appeal.original_score} б.)</div>
                <div><strong>Комментарий оператора:</strong> «${this.escapeHtml(appeal.operator_comment)}»</div>
                ${
                  appeal.status !== "pending"
                    ? `<hr style="border:none; border-top:1px solid #93c5fd; margin:4px 0;"/>
                       <div><strong>Решение проверяющего (${this.escapeHtml(appeal.reviewer_name)}):</strong> ${appeal.decision === "change" ? `Изменено на <code>${this.escapeHtml(appeal.new_answer)}</code>` : `Подтверждена оценка <code>${this.escapeHtml(appeal.original_answer)}</code>`}</div>
                       <div><strong>Причина решения:</strong> «${this.escapeHtml(appeal.reviewer_comment)}»</div>
                       <div><strong>Финальный итог карточки:</strong> <strong>${appeal.final_score_after} баллов</strong></div>`
                    : ""
                }
               </div>`
            : ""
        }

        <!-- Action Buttons: Submit Appeal (Step 05) & Reviewer Decision (Step 06) -->
        <div style="display:flex; justify-content:flex-end; gap:6px; margin-top:4px; flex-wrap:wrap;">
          <button class="btn btn-sm" onclick="App.toggleInlineAppeal('${this.escapeHtml(cid)}')">
            ⚖️ ${appeal ? "Изменить апелляцию" : "Оспорить критерий"}
          </button>
          <button class="btn btn-sm btn-primary" onclick="App.toggleInlineReview('${this.escapeHtml(cid)}')">
            ✅ Решение проверяющего
          </button>
        </div>

        <!-- Inline Form: Step 05 Submit Appeal -->
        ${
          this.state.openInlineAppealCritId === cid
            ? `<div class="appeal-form-inline">
                <div style="font-weight:700; font-size:12px; color:var(--fb-dark);">Шаг 05 · Подача апелляции по критерию «${this.escapeHtml(cr.code)} ${this.escapeHtml(cr.name)}»</div>
                <textarea class="textarea-input" id="inlineAppealComment-${this.escapeHtml(cid)}" rows="2" placeholder="Опишите аргументы оператора и укажите реплику...">${this.escapeHtml(appeal?.operator_comment || "")}</textarea>
                <div style="display:flex; justify-content:flex-end; gap:6px;">
                  <button class="btn btn-sm" onclick="App.toggleInlineAppeal(null)">Отмена</button>
                  <button class="btn btn-sm btn-primary" onclick="App.submitInlineAppeal('${this.escapeHtml(cid)}')">Сохранить обращение</button>
                </div>
               </div>`
            : ""
        }

        <!-- Inline Form: Step 06 Reviewer Decision -->
        ${
          this.state.openInlineReviewCritId === cid
            ? `<div class="appeal-form-inline">
                <div style="font-weight:700; font-size:12px; color:var(--fb-dark);">Шаг 06 · Решение проверяющего (QA Аудитор)</div>
                <div>
                  <label class="field-label">Выберите финальное значение критерия</label>
                  <select class="select-input" id="inlineReviewNewAns-${this.escapeHtml(cid)}">
                    ${reviewOptionsHtml}
                  </select>
                </div>
                <div>
                  <label class="field-label">Причина решения проверяющего (обязательно)</label>
                  <input type="text" class="text-input" id="inlineReviewReason-${this.escapeHtml(cid)}" value="${this.escapeHtml(appeal?.reviewer_comment || "Изучены доказательства в транскрипте диалога, решение принято по регламенту CQA.")}" />
                </div>
                <div style="display:flex; justify-content:flex-end; gap:6px;">
                  <button class="btn btn-sm" onclick="App.toggleInlineReview(null)">Отмена</button>
                  <button class="btn btn-sm" onclick="App.submitInlineReview('${this.escapeHtml(cid)}', 'confirm')">
                    🔒 Подтвердить исходную оценку
                  </button>
                  <button class="btn btn-sm btn-primary" onclick="App.submitInlineReview('${this.escapeHtml(cid)}', 'change')">
                    ✏️ Применить выбранную оценку и пересчитать итог
                  </button>
                </div>
               </div>`
            : ""
        }
      </div>
    `;
  },

  toggleInlineAppeal(critId) {
    this.state.openInlineAppealCritId =
      this.state.openInlineAppealCritId === critId ? null : critId;
    this.state.openInlineReviewCritId = null;
    this.renderEvaluationPane();
  },

  toggleInlineReview(critId) {
    this.state.openInlineReviewCritId =
      this.state.openInlineReviewCritId === critId ? null : critId;
    this.state.openInlineAppealCritId = null;
    this.renderEvaluationPane();
  },

  async submitInlineAppeal(critId) {
    const commentEl = document.getElementById(`inlineAppealComment-${critId}`);
    const comment = (commentEl?.value || "").trim();
    if (!comment) {
      this.showToast("Введите комментарий к апелляции");
      return;
    }

    const res = await fetch("/api/appeals/submit", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        conversation_id: this.state.currentConversationId,
        criterion_id: critId,
        comment,
        author: "Оператор линии поддержки",
      }),
    });
    const data = await res.json();
    if (!data.isSuccess) {
      this.showToast(data.error || "Ошибка сохранения апелляции");
      return;
    }

    this.showToast(data.message);
    this.state.openInlineAppealCritId = null;
    this.state.openInlineReviewCritId = critId; // Immediately open reviewer box so user can test Step 06!
    await this.loadGlobalState();
    await this.selectConversation(this.state.currentConversationId);
  },

  async submitInlineReview(critId, decisionMode) {
    const ansEl = document.getElementById(`inlineReviewNewAns-${critId}`);
    const reasonEl = document.getElementById(`inlineReviewReason-${critId}`);
    let newAnswer = ansEl ? ansEl.value : null;
    const reviewerComment = (reasonEl?.value || "").trim();

    if (!reviewerComment) {
      this.showToast("Укажите причину решения проверяющего");
      return;
    }

    if (newAnswer !== null && newAnswer !== "ДА" && newAnswer !== "НЕТ" && newAnswer !== "N/A") {
      const numVal = Number(newAnswer);
      if (!Number.isNaN(numVal)) newAnswer = numVal;
    }

    const res = await fetch("/api/appeals/decide", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        conversation_id: this.state.currentConversationId,
        criterion_id: critId,
        decision: decisionMode,
        new_answer: newAnswer,
        reviewer_comment: reviewerComment,
        reviewer_name: "QA Аудитор (Проверяющий)",
      }),
    });
    const data = await res.json();
    if (!data.isSuccess) {
      this.showToast(data.error || "Ошибка сохранения решения");
      return;
    }

    this.showToast(data.message);
    this.state.openInlineReviewCritId = null;
    await this.loadGlobalState();
    await this.loadConversationsList();
    await this.selectConversation(this.state.currentConversationId);
  },

  // ==========================================================================
  // VIEW 2: Checklist Builder (Контур 1 · 01 Создайте чек-лист)
  // ==========================================================================
  renderChecklistEditor() {
    const cl = this.state.editingChecklist;
    if (!cl) return;

    document.getElementById("clNameInput").value = cl.name || "";
    document.getElementById("clDescInput").value = cl.description || "";

    const container = document.getElementById("criteriaEditorContainer");
    const criteria = cl.criteria || [];

    let totalWeight = 0;
    criteria.forEach((c) => {
      if (c.type !== "critical_flag") totalWeight += Number(c.weight || 0);
    });

    const summaryEl = document.getElementById("checklistWeightSummary");
    summaryEl.innerHTML = `Сумма базовых весов критериев: <span style="color:${Math.abs(totalWeight - 100) < 0.01 ? "var(--fb-primary)" : "var(--warning)"};">${totalWeight.toFixed(1)} баллов</span> (при расчёте и исключении N/A система автоматически масштабирует максимум к 100.00 баллам)`;

    container.innerHTML = criteria
      .map((c, idx) => {
        const isScale = c.type === "scale";
        const isFlag = c.type === "critical_flag";

        const scaleEditorHtml = isScale
          ? `<div class="scale-steps-editor">
              <div style="display:flex; justify-content:space-between; align-items:center;">
                <span class="field-label" style="margin:0;">Ступени числовой шкалы (верхняя ступень даёт полный вес критерия)</span>
                <button class="btn btn-sm" onclick="App.addScaleStep(${idx})">➕ Добавить ступень</button>
              </div>
              ${(c.scale || [])
                .map(
                  (st, sIdx) => `
                  <div class="scale-step-row">
                    <input type="number" class="text-input" value="${st.value}" onchange="App.updateScaleStep(${idx}, ${sIdx}, 'value', this.value)" title="Балл ступени" />
                    <input type="text" class="text-input" value="${this.escapeHtml(st.label)}" onchange="App.updateScaleStep(${idx}, ${sIdx}, 'label', this.value)" placeholder="Описание ступени шкалы..." />
                    <button class="btn btn-sm" onclick="App.removeScaleStep(${idx}, ${sIdx})" title="Удалить ступень">✕</button>
                  </div>
                `
                )
                .join("")}
             </div>`
          : "";

        return `
          <div class="crit-editor-item">
            <div class="form-grid-4">
              <div>
                <label class="field-label">Код</label>
                <input type="text" class="text-input" value="${this.escapeHtml(c.code)}" onchange="App.updateCriterionField(${idx}, 'code', this.value)" />
              </div>
              <div>
                <label class="field-label">Секция чек-листа</label>
                <input type="text" class="text-input" value="${this.escapeHtml(c.section)}" onchange="App.updateCriterionField(${idx}, 'section', this.value)" />
              </div>
              <div>
                <label class="field-label">Название критерия</label>
                <input type="text" class="text-input" value="${this.escapeHtml(c.name)}" onchange="App.updateCriterionField(${idx}, 'name', this.value)" />
              </div>
              <div>
                <label class="field-label">Вес (баллы)</label>
                <input type="number" class="text-input" value="${isFlag ? 0 : c.weight}" ${isFlag ? "disabled" : ""} onchange="App.updateCriterionField(${idx}, 'weight', parseFloat(this.value || 0))" />
              </div>
              <div>
                <label class="field-label">Тип ответа</label>
                <select class="select-input" onchange="App.updateCriterionType(${idx}, this.value)">
                  <option value="yes_no" ${c.type === "yes_no" ? "selected" : ""}>ДА / НЕТ (Бинарный)</option>
                  <option value="scale" ${c.type === "scale" ? "selected" : ""}>Числовая шкала (0–N)</option>
                  <option value="critical_flag" ${c.type === "critical_flag" ? "selected" : ""}>Грубая ошибка (Без баллов)</option>
                </select>
              </div>
              <div>
                <label class="field-label">Доступность N/A</label>
                <select class="select-input" ${isFlag ? "disabled" : ""} onchange="App.updateCriterionField(${idx}, 'allow_na', this.value === 'true')">
                  <option value="false" ${!c.allow_na ? "selected" : ""}>Без N/A</option>
                  <option value="true" ${c.allow_na ? "selected" : ""}>Разрешён N/A</option>
                </select>
              </div>
              <div>
                <button class="btn btn-sm" style="color:var(--danger);" onclick="App.removeCriterion(${idx})" title="Удалить критерий">🗑️</button>
              </div>
            </div>

            <div style="display:grid; grid-template-columns: ${c.allow_na ? "1.3fr 1fr" : "1fr"}; gap:10px;">
              <div>
                <label class="field-label">Формулировка и правило оценки критерия</label>
                <input type="text" class="text-input" value="${this.escapeHtml(c.description)}" onchange="App.updateCriterionField(${idx}, 'description', this.value)" />
              </div>
              ${
                c.allow_na
                  ? `<div>
                      <label class="field-label">Условие выставления N/A</label>
                      <input type="text" class="text-input" value="${this.escapeHtml(c.na_condition || "")}" onchange="App.updateCriterionField(${idx}, 'na_condition', this.value)" placeholder="Когда критерий считается неприменимым..." />
                     </div>`
                  : ""
              }
            </div>

            ${scaleEditorHtml}
          </div>
        `;
      })
      .join("");
  },

  updateCriterionField(idx, field, val) {
    const crit = this.state.editingChecklist.criteria[idx];
    if (!crit) return;
    crit[field] = val;
    this.renderChecklistEditor();
  },

  updateCriterionType(idx, newType) {
    const crit = this.state.editingChecklist.criteria[idx];
    if (!crit) return;
    crit.type = newType;
    if (newType === "critical_flag") {
      crit.weight = 0;
      crit.allow_na = false;
      crit.scale = [];
    } else if (newType === "scale" && (!crit.scale || crit.scale.length === 0)) {
      crit.weight = crit.weight || 15;
      crit.scale = [
        { value: 10, label: "Все требования критерия полностью соблюдены." },
        { value: 5, label: "Требование выполнено частично, допущена неточность." },
        { value: 0, label: "Требование не выполнено." },
      ];
    } else if (newType === "yes_no") {
      crit.weight = crit.weight || 10;
    }
    this.renderChecklistEditor();
  },

  addScaleStep(critIdx) {
    const crit = this.state.editingChecklist.criteria[critIdx];
    if (!crit) return;
    crit.scale = crit.scale || [];
    crit.scale.push({ value: 0, label: "Новая ступень шкалы" });
    this.renderChecklistEditor();
  },

  updateScaleStep(critIdx, stepIdx, field, val) {
    const crit = this.state.editingChecklist.criteria[critIdx];
    if (!crit || !crit.scale || !crit.scale[stepIdx]) return;
    crit.scale[stepIdx][field] = field === "value" ? parseFloat(val || 0) : val;
    this.renderChecklistEditor();
  },

  removeScaleStep(critIdx, stepIdx) {
    const crit = this.state.editingChecklist.criteria[critIdx];
    if (!crit || !crit.scale) return;
    crit.scale.splice(stepIdx, 1);
    this.renderChecklistEditor();
  },

  addBlankCriterion() {
    const cl = this.state.editingChecklist;
    const nextNum = cl.criteria.length + 1;
    const codeStr = String(nextNum).padStart(2, "0");
    cl.criteria.push({
      id: `c_custom_${Date.now()}`,
      code: codeStr,
      section: "ВЕДЕНИЕ И ЗАВЕРШЕНИЕ ДИАЛОГА",
      name: "Оперативность первого ответа оператора (SLA до 2 минут)",
      description:
        "Да — оператор ответил клиенту быстрее чем за 2 минуты (120 секунд) после эскалации. Нет — ожидание превысило 2 минуты.",
      type: "yes_no",
      weight: 10.0,
      allow_na: true,
      na_condition: "Временные метки эскалации отсутствуют.",
      scale: [],
    });
    this.renderChecklistEditor();
    this.showToast("Добавлен новый критерий в конец чек-листа");
  },

  removeCriterion(idx) {
    this.state.editingChecklist.criteria.splice(idx, 1);
    this.renderChecklistEditor();
  },

  applyPresetCriterionSwap(presetIdx) {
    const tpl = this.state.presetTemplates[presetIdx];
    if (!tpl) return;
    const cl = this.state.editingChecklist;

    if (presetIdx === 0) {
      // Replace criterion 07 (Завершение) with SLA speed criterion
      const targetIdx = cl.criteria.findIndex((c) => c.code === "07");
      const newCrit = JSON.parse(JSON.stringify(tpl));
      newCrit.code = "07";
      newCrit.weight = 5.0;
      if (targetIdx >= 0) cl.criteria[targetIdx] = newCrit;
      else cl.criteria.push(newCrit);
      cl.name = "Чек-лист CQA (Контрольная версия: с оценкой SLA первого ответа)";
    } else if (presetIdx === 1) {
      // Replace criterion 02 with Self-Service Navigation scale criterion
      const targetIdx = cl.criteria.findIndex((c) => c.code === "02");
      const newCrit = JSON.parse(JSON.stringify(tpl));
      newCrit.code = "02";
      newCrit.weight = 15.0;
      if (targetIdx >= 0) cl.criteria[targetIdx] = newCrit;
      else cl.criteria.push(newCrit);
      cl.name = "Чек-лист CQA (Контрольная версия: со шкалой обучения навигации)";
    }

    this.renderChecklistEditor();
    this.showToast("Критерий заменён! Нажмите «Сохранить чек-лист и пересчитать диалоги».");
  },

  async resetChecklistToDefault() {
    const res = await fetch("/api/checklists");
    const data = await res.json();
    const def = (data.checklists || []).find((c) => c.id === "default-freedom-cqa-v1");
    if (def) {
      this.state.editingChecklist = JSON.parse(JSON.stringify(def));
      this.renderChecklistEditor();
      await this.saveAndApplyChecklist();
    }
  },

  async saveAndApplyChecklist() {
    const cl = this.state.editingChecklist;
    cl.name = document.getElementById("clNameInput").value || cl.name;
    cl.description = document.getElementById("clDescInput").value || cl.description;

    const res = await fetch("/api/checklists", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        checklist: cl,
        apply_to_all: true,
      }),
    });
    const data = await res.json();
    if (!data.isSuccess) {
      this.showToast(data.error || "Ошибка сохранения чек-листа", "error");
      return;
    }

    this.showToast(data.message, "success");
    await this.loadGlobalState();
    await this.loadConversationsList();
    if (this.state.currentConversationId) {
      await this.selectConversation(this.state.currentConversationId);
    }
    if (data.job) this.trackJob(data.job);
  },

  // ==========================================================================
  // VIEW 3: Appeals & QA Review Queue
  // ==========================================================================
  async renderAppealsView() {
    const container = document.getElementById("appealsQueueContainer");
    if (!container) return;

    const res = await fetch("/api/appeals");
    const data = await res.json();
    const appeals = data.appeals || [];

    if (appeals.length === 0) {
      container.innerHTML = `<div class="card-panel" style="text-align:center; color:var(--text-muted);">Активных или рассмотренных апелляций пока нет.</div>`;
      return;
    }

    container.innerHTML = appeals
      .map((a) => {
        const isPending = a.status === "pending";
        return `
          <div class="card-panel" style="border-left: 5px solid ${isPending ? "var(--warning)" : "var(--info)"};">
            <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:10px;">
              <div>
                <span class="mini-badge ${isPending ? "appeal-pending" : "appeal-done"}">
                  ${isPending ? "⏳ Ожидает решения проверяющего" : a.status === "changed" ? "✅ Удовлетворена (Оценка изменена)" : "🔒 Отклонена (Оценка подтверждена)"}
                </span>
                <strong style="font-size:15px; margin-left:8px;">Диалог: ${this.escapeHtml(a.conversation_id)} (${this.escapeHtml(a.language)}, ${this.escapeHtml(a.channel)})</strong>
                <span style="color:var(--text-secondary); margin-left:8px;">· Критерий ${this.escapeHtml(a.criterion_code)}: <strong>${this.escapeHtml(a.criterion_name)}</strong></span>
              </div>
              <button class="btn btn-sm btn-primary" onclick="App.openConversationFromAppeal('${this.escapeHtml(a.conversation_id)}', '${this.escapeHtml(a.criterion_id)}')">
                Перейти к карточке и доказательствам →
              </button>
            </div>

            <div style="margin-top:10px; display:grid; grid-template-columns: 1fr 1fr; gap:12px; background:var(--bg-subtle); padding:12px; border-radius:8px;">
              <div>
                <div style="font-size:11.5px; color:var(--text-secondary); font-weight:700;">Обращение оператора (${this.escapeHtml(a.author)})</div>
                <div style="margin-top:4px;"><strong>Оспариваемая оценка критерия:</strong> <code>${this.escapeHtml(a.original_answer)}</code> (Исходный итог диалога: <strong>${a.initial_total_score} б.</strong>)</div>
                <div style="margin-top:4px;"><strong>Аргументация:</strong> «${this.escapeHtml(a.operator_comment)}»</div>
              </div>
              <div>
                <div style="font-size:11.5px; color:var(--text-secondary); font-weight:700;">Решение проверяющего</div>
                ${
                  !isPending
                    ? `<div style="margin-top:4px;"><strong>Статус:</strong> ${a.decision === "change" ? `Оценка изменена на <code>${this.escapeHtml(a.new_answer)}</code>` : `Оставлена без изменений (<code>${this.escapeHtml(a.original_answer)}</code>)`}</div>
                       <div style="margin-top:4px;"><strong>Комментарий проверяющего (${this.escapeHtml(a.reviewer_name)}):</strong> «${this.escapeHtml(a.reviewer_comment)}»</div>
                       <div style="margin-top:4px; color:var(--fb-dark);"><strong>Финальный балл после пересмотра: ${a.current_total_score} б.</strong></div>`
                    : `<div style="margin-top:6px; display:flex; gap:8px; flex-wrap:wrap;">
                        <button class="btn btn-sm" onclick="App.quickDecideFromQueue('${this.escapeHtml(a.conversation_id)}', '${this.escapeHtml(a.criterion_id)}', 'confirm', '${this.escapeHtml(a.original_answer)}')">
                          🔒 Подтвердить исходную оценку
                        </button>
                        <button class="btn btn-sm btn-primary" onclick="App.quickDecideFromQueue('${this.escapeHtml(a.conversation_id)}', '${this.escapeHtml(a.criterion_id)}', 'change', 'ДА')">
                          ✅ Удовлетворить апелляцию (Изменить на «ДА» / макс. балл)
                        </button>
                       </div>`
                }
              </div>
            </div>
          </div>
        `;
      })
      .join("");
  },

  async openConversationFromAppeal(cid, critId) {
    this.switchView("workspace");
    await this.selectConversation(cid);
    this.state.openInlineReviewCritId = critId;
    this.renderEvaluationPane();
    const cardEl = document.getElementById(`crit-card-${critId}`);
    if (cardEl) cardEl.scrollIntoView({ behavior: "smooth", block: "center" });
  },

  async quickDecideFromQueue(cid, critId, decision, newAns) {
    const res = await fetch("/api/appeals/decide", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        conversation_id: cid,
        criterion_id: critId,
        decision: decision,
        new_answer: newAns,
        reviewer_comment:
          decision === "change"
            ? "Апелляция удовлетворена после проверки контекста реплик в транскрипте."
            : "После проверки реплик исходная оценка признана корректной по правилам чек-листа.",
        reviewer_name: "Старший проверяющий QA",
      }),
    });
    const data = await res.json();
    if (data.isSuccess) {
      this.showToast(data.message);
      await this.loadGlobalState();
      await this.loadConversationsList();
      await this.renderAppealsView();
      if (this.state.currentConversationId === cid) {
        await this.selectConversation(cid);
      }
    }
  },

  // ==========================================================================
  // VIEW 4: Analytics, AI Cost Calculator & Dynamics 365 Web Resource Info
  // ==========================================================================
  renderAnalyticsView() {
    const container = document.getElementById("analyticsPageContainer");
    if (!container) return;

    const st = this.state.stats || {};
    const llm = this.state.llmConfig || {};
    const critConvs = this.state.conversations.filter((c) => c.has_critical_error);
    const naConvs = this.state.conversations.filter((c) => c.na_count > 0).slice(0, 8);

    container.innerHTML = `
      <div class="kpi-grid">
        <div class="kpi-card">
          <div class="score-box-label">Всего оценено диалогов</div>
          <div class="kpi-value">${st.total_conversations || 65}</div>
          <div class="score-box-sub">60 чатов + 5 звонков (RU / KZ / EN)</div>
        </div>
        <div class="kpi-card">
          <div class="score-box-label">Средний балл качества</div>
          <div class="kpi-value" style="color:var(--fb-primary);">${st.average_score || 0} <span style="font-size:15px;">/ 100</span></div>
          <div class="score-box-sub">Dynamic Weight Rebalancing при N/A</div>
        </div>
        <div class="kpi-card">
          <div class="score-box-label">Critical Fail Override (Крит. 08)</div>
          <div class="kpi-value" style="color:var(--danger);">${st.critical_errors_count || 0}</div>
          <div class="score-box-sub">Блокировка допуска оператора</div>
        </div>
        <div class="kpi-card">
          <div class="score-box-label">Байесовский аудит Beta(α, β)</div>
          <div class="kpi-value" style="color:#b45309;">${st.supervisor_audit_recommended_count || 0}</div>
          <div class="score-box-sub">Ср. достоверность: ${st.average_bayesian_confidence || 92}% · VaR₀.₉₅: ${st.average_var_95_sec || 0}с</div>
        </div>
      </div>

      <!-- 4 Interconnected Classes of Algorithms Card -->
      <div class="card-panel">
        <h3 style="font-size:16px; font-weight:800; color:var(--fb-dark); margin-bottom:6px;">
          🧮 Архитектура гибридного решения: 4 взаимосвязанных класса алгоритмов
        </h3>
        <p style="font-size:12.5px; color:var(--text-secondary); margin-bottom:12px;">
          Математическое ядро CQA объединяет детерминированную агрегацию, актуарную теорию вероятностных рисков, управляемое декодирование LLM со строгой грамматикой и цифровую обработку акустических сигналов (CMU PocketSphinx).
        </p>

        <div style="display:grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap:12px;">
          <div class="score-box" style="border-left:4px solid #047857;">
            <span class="score-box-label" style="color:#047857;">1 · Детерминированные математические алгоритмы</span>
            <div style="font-size:12px; margin-top:6px; line-height:1.45;">
              <div>• <strong>Dynamic Weight Rebalancing:</strong> при выпадении критерия в <code>N/A</code> активный вес <code>W_active = Σ w_j</code> масштабирует баллы <code>round(Σ (S_scaled / W_active) × 100, 2) ∈ [0, 100]</code> (${st.na_dialogues_count || 0} диалогов).</div>
              <div style="margin-top:4px;">• <strong>Critical Fail Override:</strong> булева маска для критерия <code>08</code>, блокирующая допуск оператора к аудиту без искажения весовой шкалы.</div>
              <div style="margin-top:4px;">• <strong>Time-Delta Verification:</strong> парсинг <code>MM:SS</code> и <code>ISO 8601</code>; при <code>Δt_first &gt; 180 с</code> накладывается верхняя граница <code>Score_06 ≤ 7</code>.</div>
            </div>
          </div>

          <div class="score-box" style="border-left:4px solid #1d4ed8;">
            <span class="score-box-label" style="color:#1d4ed8;">2 · Актуарные алгоритмы и теория вероятностных рисков</span>
            <div style="font-size:12px; margin-top:6px; line-height:1.45;">
              <div>• <strong>Empirical Quantiles & Weibull VaR₀.₉₅:</strong> оценка «тяжёлого хвоста» задержек <code>VaR_0.95 = inf{t : P(Δt &gt; t) ≤ 0.05}</code> (средний по набору: <strong>${st.average_var_95_sec || 0} с</strong>).</div>
              <div style="margin-top:4px;">• <strong>Collective Risk Model:</strong> индекс операционного риска <code>Risk Index = Σ w_i · (1 − score_i / max_score_i)</code> (средний риск: <strong>${st.average_risk_index || 0} б.</strong>).</div>
              <div style="margin-top:4px;">• <strong>Bayesian Confidence Engine:</strong> моделирование через <code>Beta(α, β)</code>; при превышении порога дисперсии выставляется флаг <em>«Рекомендован выборочный аудит супервизором»</em> (${st.supervisor_audit_recommended_count || 0} шт.).</div>
            </div>
          </div>

          <div class="score-box" style="border-left:4px solid #7c3aed;">
            <span class="score-box-label" style="color:#7c3aed;">3 · Алгоритмы NLP и структурированного вывода (LLM)</span>
            <div style="font-size:12px; margin-top:6px; line-height:1.45;">
              <div>• <strong>Constrained JSON Decoding (FSA):</strong> конечный автомат состояний (Pydantic / JSON Schema), ограничивающий <code>Score_04 ∈ {0, 5, 10, 15, 20, "N/A"}</code> и <code>Score_06 ∈ {0, 4, 7, 10}</code>.</div>
              <div style="margin-top:4px;">• <strong>Targeted Evidence Extraction:</strong> жёсткая привязка каждого вывода к индексу реплики <code>evidence_line_index</code>, точной цитате <code>evidence_quote</code> и DOM-селектору <code>#turn-k</code>.</div>
            </div>
          </div>

          <div class="score-box" style="border-left:4px solid #4338ca;">
            <span class="score-box-label" style="color:#4338ca;">4 · Акустические алгоритмы и ASR (CMU PocketSphinx)</span>
            <div style="font-size:12px; margin-top:6px; line-height:1.45;">
              <div>• <strong>Spectral VAD & RMS dBFS Profiling:</strong> нормализация потока в <code>16 kHz 16-bit mono PCM</code>, выделение речевых сегментов <code>pocketsphinx.Segmenter</code> и расчёт энергии <code>20·log10(RMS / 32768) dBFS</code>.</div>
              <div style="margin-top:4px;">• <strong>HMM-GMM Viterbi Beam Decoding:</strong> поиск MAP-гипотезы <code>argmax_W P(O|W)·P(W)^α</code> в <code>pocketsphinx.Decoder</code> с покадровым выравниванием слов (шаг 10 мс).</div>
              <div style="margin-top:4px;">• <strong>Bilingual Code-Switching Repair:</strong> фонетическая реконструкция KZ/RU переключений кода в телефонных звонках (<code>p4-ru-003</code>).</div>
            </div>
          </div>
        </div>
      </div>

      <!-- AI Model & Cost per 1 Evaluation Card (FAQ Requirement) -->
      <div class="card-panel">
        <h3 style="font-size:16px; font-weight:800; color:var(--fb-dark); margin-bottom:8px;">
          🤖 Сервис оценки, ИИ-модель и расчёт стоимости одной оценки (Требование FAQ)
        </h3>
        <p style="font-size:12.5px; color:var(--text-secondary); margin-bottom:12px;">
          Система поддерживает гибридный режим: встроенный многоязычный (RU / KZ / EN) аналитический движок правил + подключение внешних LLM API (Google Gemini 2.5 Flash / Pro, OpenAI GPT-4o-mini, локальная модель через Ollama).
        </p>

        <div style="display:grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap:12px; margin-bottom:14px;">
          <div class="score-box">
            <span class="score-box-label">Рекомендуемая платная API модель</span>
            <strong style="font-size:15px; margin-top:4px; color:var(--fb-dark);">Google Gemini 2.5 Flash (или GPT-4o-mini)</strong>
            <span class="score-box-sub">1 вызов со структурированной JSON-схемой на 1 диалог (все 8 критериев за 1 проход)</span>
          </div>
          <div class="score-box">
            <span class="score-box-label">Средний объём токенов на 1 оценку</span>
            <strong style="font-size:15px; margin-top:4px; color:var(--fb-dark);">~1 150 вх. токенов + ~420 вых. токенов</strong>
            <span class="score-box-sub">Промпт чек-листа + транскрипт диалога + JSON обоснований с цитатами</span>
          </div>
          <div class="score-box final-highlight">
            <span class="score-box-label">Стоимость 1 оценки (со всеми вызовами)</span>
            <strong style="font-size:16px; margin-top:4px; color:var(--fb-dark);">~$0.00042 USD (≈ 0.21 ₸ / диалог)</strong>
            <span class="score-box-sub">Оценка всего набора из 65 диалогов: ~$0.027 USD (≈ 13.6 ₸)</span>
          </div>
        </div>

        <div class="llm-settings" id="llmSettingsPanel">
          <div class="llm-status-row">
            <span class="llm-status-pill ${llm.llm_active ? "on" : "off"}">
              ${llm.llm_active
                ? `🟢 Активен: ${this.escapeHtml(llm.providers?.[llm.provider]?.label || llm.provider)} · ${this.escapeHtml(llm.model)}`
                : llm.provider && llm.provider !== "builtin"
                ? `🟠 ${this.escapeHtml(llm.providers?.[llm.provider]?.label || llm.provider)} выбран, но API-ключ не задан — работает встроенный движок`
                : "⚪ Работает встроенный аналитический движок (без внешнего LLM)"}
            </span>
            <span id="llmTestResult" class="llm-test-result"></span>
          </div>
          <div class="llm-settings-grid">
            <div>
              <label class="field-label" for="llmProviderSelect">Режим / Провайдер</label>
              <select class="select-input" id="llmProviderSelect" onchange="App.onLlmProviderChange()">
                <option value="builtin" ${llm.provider === "builtin" ? "selected" : ""}>Гибридный аналитический движок (Offline/Instant)</option>
                <option value="gemini" ${llm.provider === "gemini" ? "selected" : ""}>Google Gemini API</option>
                <option value="openai" ${llm.provider === "openai" ? "selected" : ""}>OpenAI / Compatible API</option>
                <option value="ollama" ${llm.provider === "ollama" ? "selected" : ""}>Локальная модель (Ollama)</option>
              </select>
            </div>
            <div>
              <label class="field-label" for="llmModelInput">Модель</label>
              <input type="text" class="text-input" id="llmModelInput" list="llmModelSuggestions" value="${this.escapeHtml(llm.model || "gemini-flash-latest")}" />
              <datalist id="llmModelSuggestions">
                <option value="gemini-flash-latest"></option>
                <option value="gemini-flash-lite-latest"></option>
                <option value="gemini-pro-latest"></option>
                <option value="gpt-4o-mini"></option>
                <option value="llama3.1:8b"></option>
              </datalist>
            </div>
            <div class="llm-key-field ${llm.provider === "ollama" || llm.provider === "builtin" ? "is-muted" : ""}">
              <label class="field-label" for="llmKeyInput">
                API-ключ ${llm.has_api_key ? `<span class="llm-key-saved">сохранён: ${this.escapeHtml(llm.api_key_masked)}</span>` : llm.api_key_from_env ? `<span class="llm-key-saved">из переменной окружения</span>` : ""}
              </label>
              <input type="password" class="text-input" id="llmKeyInput" autocomplete="off" value=""
                placeholder="${llm.has_api_key ? "Оставьте пустым, чтобы не менять ключ" : "Вставьте ключ Gemini (AIza… / AQ.…) — можно целиком curl из AI Studio"}" />
            </div>
            <div class="llm-baseurl-field ${llm.provider === "openai" || llm.provider === "ollama" ? "" : "hidden"}">
              <label class="field-label" for="llmBaseUrlInput">Base URL (необязательно)</label>
              <input type="text" class="text-input" id="llmBaseUrlInput" value="${this.escapeHtml(llm.base_url || "")}"
                placeholder="${llm.provider === "ollama" ? "http://127.0.0.1:11434/api/chat" : "https://api.openai.com/v1/chat/completions"}" />
            </div>
          </div>
          <div class="llm-actions">
            <label class="llm-checkbox"><input type="checkbox" id="llmReevalCheckbox" checked /> Переоценить все диалоги выбранной моделью после сохранения</label>
            <div style="display:flex; gap:8px; flex-wrap:wrap;">
              ${llm.has_api_key ? `<button class="btn" onclick="App.clearLlmKey()" title="Удалить сохранённый ключ">🗑 Удалить ключ</button>` : ""}
              <button class="btn" id="llmTestBtn" onclick="App.testLlmSettings()">🔌 Проверить подключение</button>
              <button class="btn btn-primary" id="llmSaveBtn" onclick="App.saveLlmSettings()">💾 Сохранить</button>
            </div>
          </div>
        </div>
      </div>

      <!-- Critical Errors Summary Table -->
      <div class="card-panel">
        <h3 style="font-size:16px; font-weight:800; color:var(--danger); margin-bottom:8px;">
          🚨 Выявленные диалоги с грубыми ошибками (Критерий 08 — ДА)
        </h3>
        <div style="display:grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap:10px;">
          ${critConvs
            .map(
              (c) => `
              <div class="score-box" style="border-color:var(--danger-border); background:var(--danger-bg); cursor:pointer;" onclick="App.switchView('workspace'); App.selectConversation('${this.escapeHtml(c.conversation_id)}')">
                <div style="display:flex; justify-content:space-between;">
                  <strong>${this.escapeHtml(c.conversation_id)} (${this.escapeHtml(c.language)})</strong>
                  <span class="mini-badge crit">Балл: ${c.final_score}</span>
                </div>
                <div style="font-size:12px; margin-top:4px; color:var(--text-secondary);">${this.escapeHtml(c.client_preview)}</div>
                <div style="font-size:11px; font-weight:700; color:var(--danger); margin-top:4px;">Открыть доказательство →</div>
              </div>
            `
            )
            .join("")}
        </div>
      </div>

      <!-- Microsoft Dynamics 365 Web Resource Architecture Note -->
      <div class="card-panel">
        <h3 style="font-size:16px; font-weight:800; color:var(--fb-dark); margin-bottom:6px;">
          🧩 Готовность к встраиванию как HTML Web Resource в Microsoft Dynamics 365
        </h3>
        <p style="font-size:12.5px; color:var(--text-secondary);">
          Интерфейс написан на чистом <strong>HTML5, CSS3 и Vanilla JavaScript</strong> без тяжёлых сборщиков и включает адаптер <code>public/d365-bridge.js</code>. При встраивании в карточку <code>msdyn_ocliveworkitem</code> (Customer Service Workspace) компонент автоматически считывает параметр <code>?data=conversation_id%3D...</code> или контекст <code>window.parent.Xrm.Page.data.entity</code> и отправляет события пересмотра оценки в родительскую форму Dynamics 365.
        </p>
      </div>
    `;
  },

  // ==========================================================================
  // LLM settings (Gemini / OpenAI / Ollama) & background batch jobs
  // ==========================================================================
  readLlmForm() {
    return {
      provider: document.getElementById("llmProviderSelect")?.value || "builtin",
      model: (document.getElementById("llmModelInput")?.value || "").trim(),
      api_key: (document.getElementById("llmKeyInput")?.value || "").trim(),
      base_url: (document.getElementById("llmBaseUrlInput")?.value || "").trim(),
    };
  },

  onLlmProviderChange() {
    const provider = document.getElementById("llmProviderSelect")?.value || "builtin";
    const defaults = { builtin: "gemini-flash-latest", gemini: "gemini-flash-latest", openai: "gpt-4o-mini", ollama: "llama3.1:8b" };
    const modelInput = document.getElementById("llmModelInput");
    const current = (modelInput?.value || "").trim();
    const isForeign =
      !current ||
      (provider === "gemini" && !current.startsWith("gemini")) ||
      (provider !== "gemini" && provider !== "builtin" && current.startsWith("gemini"));
    if (modelInput && isForeign) modelInput.value = defaults[provider];

    const baseField = document.querySelector(".llm-baseurl-field");
    if (baseField) baseField.classList.toggle("hidden", !(provider === "openai" || provider === "ollama"));
    const keyField = document.querySelector(".llm-key-field");
    if (keyField) keyField.classList.toggle("is-muted", provider === "ollama" || provider === "builtin");
    this.setLlmTestResult("", "");
  },

  setLlmTestResult(text, kind) {
    const el = document.getElementById("llmTestResult");
    if (!el) return;
    el.textContent = text;
    el.className = `llm-test-result ${kind || ""}`;
  },

  async postJson(url, body) {
    try {
      const res = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body || {}),
      });
      const data = await res.json().catch(() => ({ isSuccess: false, error: `HTTP ${res.status}` }));
      if (!res.ok && data.isSuccess !== false) data.isSuccess = false;
      return data;
    } catch (err) {
      return { isSuccess: false, error: `Сервер недоступен: ${err.message}` };
    }
  },

  setButtonBusy(id, busy, busyText) {
    const btn = document.getElementById(id);
    if (!btn) return;
    if (busy) {
      btn.dataset.label = btn.innerHTML;
      btn.innerHTML = busyText;
      btn.disabled = true;
    } else {
      if (btn.dataset.label) btn.innerHTML = btn.dataset.label;
      btn.disabled = false;
    }
  },

  async testLlmSettings() {
    const form = this.readLlmForm();
    if (form.provider === "builtin") {
      this.setLlmTestResult("Встроенный движок не требует подключения", "ok");
      return;
    }
    this.setButtonBusy("llmTestBtn", true, "⏳ Проверка…");
    this.setLlmTestResult("Отправляем тестовый запрос…", "");
    const data = await this.postJson("/api/settings/llm/test", form);
    this.setButtonBusy("llmTestBtn", false);
    if (!data.isSuccess) {
      this.setLlmTestResult(data.error || "Ошибка проверки", "error");
      return;
    }
    this.setLlmTestResult(data.test?.message || "", data.test?.ok ? "ok" : "error");
  },

  async saveLlmSettings() {
    const form = this.readLlmForm();
    const reevaluate = Boolean(document.getElementById("llmReevalCheckbox")?.checked);
    this.setButtonBusy("llmSaveBtn", true, "⏳ Сохранение…");
    if (form.provider !== "builtin") this.setLlmTestResult("Проверяем подключение…", "");
    const data = await this.postJson("/api/settings/llm", { ...form, test: true, reevaluate_all: reevaluate });
    this.setButtonBusy("llmSaveBtn", false);

    if (!data.isSuccess) {
      this.setLlmTestResult(data.error || "Ошибка сохранения", "error");
      this.showToast(data.error || "Ошибка сохранения настроек", "error");
      return;
    }
    const testFailed = data.test && !data.test.ok;
    this.showToast(data.message, testFailed ? "error" : "success");
    await this.loadGlobalState();
    if (this.state.activeView === "analytics") this.renderAnalyticsView();
    if (data.test) this.setLlmTestResult(data.test.message, data.test.ok ? "ok" : "error");
    if (data.job) this.trackJob(data.job);
  },

  async clearLlmKey() {
    if (!confirm("Удалить сохранённый API-ключ?")) return;
    const data = await this.postJson("/api/settings/llm", { clear_api_key: true, test: false });
    if (data.isSuccess) {
      this.showToast("API-ключ удалён", "success");
      await this.loadGlobalState();
      this.renderAnalyticsView();
    } else {
      this.showToast(data.error || "Ошибка", "error");
    }
  },

  trackJob(job) {
    if (!job) return;
    this.state.trackedJobId = job.id;
    this.renderJobProgress(job);
    if (this._jobTimer) return;
    this._jobTimer = setInterval(() => this.pollJob(), 1500);
  },

  async pollJob() {
    let data;
    try {
      data = await (await fetch("/api/jobs/current")).json();
    } catch (_) {
      return;
    }
    const job = data && data.job;
    if (!job || job.id !== this.state.trackedJobId) {
      clearInterval(this._jobTimer);
      this._jobTimer = null;
      this.renderJobProgress(null);
      return;
    }
    this.renderJobProgress(job);
    if (job.status !== "running") {
      clearInterval(this._jobTimer);
      this._jobTimer = null;
      const msg =
        job.status === "cancelled"
          ? `LLM-оценка остановлена: обработано ${job.done} из ${job.total}`
          : `LLM-оценка завершена: ${job.llm_ok} диалогов оценено моделью ${job.model}` +
            (job.fallback ? `, ${job.fallback} — встроенным движком (ошибки API)` : "");
      this.showToast(msg, job.fallback ? "error" : "success");
      setTimeout(() => this.renderJobProgress(null), job.fallback ? 12000 : 4000);
      await this.refreshAfterEvaluation();
    } else if (job.done && job.done % 5 === 0) {
      this.loadConversationsList();
    }
  },

  renderJobProgress(job) {
    let el = document.getElementById("llmJobProgress");
    if (!job) {
      if (el) el.remove();
      return;
    }
    if (!el) {
      el = document.createElement("div");
      el.id = "llmJobProgress";
      el.className = "job-progress";
      document.body.appendChild(el);
    }
    const pct = job.total ? Math.round((job.done / job.total) * 100) : 0;
    const running = job.status === "running";
    el.innerHTML = `
      <div class="job-progress-head">
        <strong>🤖 ${this.escapeHtml(job.reason || "LLM-оценка")}</strong>
        ${running ? `<button class="btn btn-sm" onclick="App.cancelJob()">Остановить</button>` : `<button class="btn btn-sm" onclick="App.renderJobProgress(null)">✕</button>`}
      </div>
      <div class="job-progress-sub">${this.escapeHtml(job.model)} · ${job.done} / ${job.total} · ✅ ${job.llm_ok}${job.fallback ? ` · ⚠️ ${job.fallback}` : ""}</div>
      <div class="job-progress-bar"><div style="width:${pct}%"></div></div>
      ${(job.errors || []).length ? `<div class="job-progress-err" title="${this.escapeHtml(job.errors.join("\n"))}">${this.escapeHtml(job.errors[job.errors.length - 1])}</div>` : ""}
    `;
  },

  async cancelJob() {
    await this.postJson("/api/jobs/cancel", {});
  },

  async refreshAfterEvaluation() {
    await this.loadGlobalState();
    await this.loadConversationsList();
    if (this.state.currentConversationId) await this.selectConversation(this.state.currentConversationId);
    if (this.state.activeView === "analytics") this.renderAnalyticsView();
  },

  // ==========================================================================
  // Navigation, Pitch Guided Steps & Modals
  // ==========================================================================
  switchView(viewName) {
    this.state.activeView = viewName;
    document.querySelectorAll(".nav-tab").forEach((btn) => {
      btn.classList.toggle("active", btn.getAttribute("data-view") === viewName);
    });
    document.querySelectorAll(".view-container").forEach((sec) => {
      sec.classList.toggle("active", sec.id === `view-${viewName}`);
    });

    if (viewName === "checklist") this.renderChecklistEditor();
    else if (viewName === "appeals") this.renderAppealsView();
    else if (viewName === "analytics") this.renderAnalyticsView();
  },

  toggleD365Mode() {
    document.body.classList.toggle("d365-mode");
    const isOn = document.body.classList.contains("d365-mode");
    this.showToast(
      isOn
        ? "Включён режим предпросмотра HTML-компонента Microsoft Dynamics 365"
        : "Обычный полноэкранный режим"
    );
  },

  async runPitchStep(stepNum) {
    if (stepNum === 1) {
      // 1 minute: Show interface and a voice call with audio & timestamp seeking
      this.switchView("workspace");
      this.setQuickFilter("");
      await this.selectConversation("p4-ru-003");
      this.showToast("Шаг 1: Открыт звонок p4-ru-003 с аудиоплеером и привязкой реплик ко времени");
    } else if (stepNum === 2) {
      // 2 minutes: Show N/A weight redistribution (p1-ru-025) or rough error (p1-ru-009)
      this.switchView("workspace");
      const nextTarget =
        this.state.currentConversationId === "p1-ru-025" ? "p1-ru-009" : "p1-ru-025";
      await this.selectConversation(nextTarget);
      if (nextTarget === "p1-ru-025") {
        this.showToast(
          "Шаг 2 (Пример N/A): В p1-ru-025 клиент вышел до вопроса — 4 критерия N/A, веса пропорционально перераспределены к 100 баллам! (Нажмите ещё раз для примера Грубой ошибки p1-ru-009)"
        );
      } else {
        this.showToast(
          "Шаг 2 (Грубая ошибка 🚨): В p1-ru-009 оператор раскрыл ограничение по счёту без идентификации клиента (Реплика [11])"
        );
        this.jumpToTurn(11, 0);
      }
    } else if (stepNum === 3) {
      // 1 minute: Show appeal and reviewer decision with score recalculation
      this.switchView("workspace");
      await this.selectConversation("p1-ru-001");
      this.state.openInlineReviewCritId = "c07";
      this.renderEvaluationPane();
      const cardEl = document.getElementById("crit-card-c07");
      if (cardEl) cardEl.scrollIntoView({ behavior: "smooth", block: "center" });
      this.showToast(
        "Шаг 3: Открыта апелляция по критерию «07 Завершение» в p1-ru-001. Выберите «ДА» и нажмите «Применить выбранную оценку», чтобы увидеть пересчёт с 92.31 до 100.00!"
      );
    } else if (stepNum === 4) {
      // 1 minute: Show changing a criterion in the checklist and applying to dialogues
      this.switchView("checklist");
      this.showToast(
        "Шаг 4: Конструктор чек-листа. Нажмите кнопку быстрой замены критерия или отредактируйте любой критерий и сохраните!"
      );
    }
  },

  async evaluateCurrentConversation() {
    if (!this.state.currentConversationId) return;
    const llm = this.state.llmConfig || {};
    if (llm.llm_active) this.showToast(`Оценка через ${llm.model}… это может занять 5–30 секунд`);
    const data = await this.postJson("/api/evaluate", { conversation_id: this.state.currentConversationId });
    if (!data.isSuccess) {
      this.showToast(data.error || "Ошибка оценки", "error");
      return;
    }
    const fellBack = llm.llm_active && !data.llm_used;
    this.showToast(data.message, fellBack ? "error" : "success");
    await this.refreshAfterEvaluation();
  },

  async evaluateAllConversations() {
    const llm = this.state.llmConfig || {};
    if (!llm.llm_active) this.showToast("Запущена пакетная оценка всех диалогов по активному чек-листу...");
    const data = await this.postJson("/api/evaluate", { all: true });
    if (!data.isSuccess) {
      this.showToast(data.error || "Ошибка пакетной оценки", "error");
      return;
    }
    this.showToast(data.message, "success");
    if (data.job) {
      this.trackJob(data.job);
    } else {
      await this.refreshAfterEvaluation();
    }
  },

  openApiFetchModal() {
    document.getElementById("apiFetchModal").classList.add("open");
  },

  openUploadModal() {
    document.getElementById("uploadJsonModal").classList.add("open");
  },

  openNewAppealModal() {
    const conv = this.state.currentConversation;
    if (!conv) return;
    const select = document.getElementById("appealCriterionSelect");
    const results = conv.current_evaluation?.criteria_results || [];
    select.innerHTML = results
      .map(
        (cr) =>
          `<option value="${this.escapeHtml(cr.criterion_id)}">${this.escapeHtml(cr.code)} · ${this.escapeHtml(cr.name)} (Текущая оценка: ${this.escapeHtml(cr.answer)})</option>`
      )
      .join("");
    document.getElementById("appealCommentInput").value = "";
    document.getElementById("newAppealModal").classList.add("open");
  },

  closeModals() {
    document.querySelectorAll(".modal-backdrop").forEach((m) => m.classList.remove("open"));
  },

  async submitApiFetch() {
    const groupId = document.getElementById("apiGroupIdInput").value.trim() || "DEV";
    const apiUrl = document.getElementById("apiCustomUrlInput").value.trim();
    const btn = document.getElementById("btnRunApiFetch");
    btn.disabled = true;
    btn.textContent = "⏳ Загрузка и оценка...";

    try {
      const res = await fetch("/api/datasets/fetch", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ group_id: groupId, api_url: apiUrl }),
      });
      const data = await res.json();
      if (!data.isSuccess) {
        this.showToast(data.error || "Ошибка запроса к API", "error");
      } else {
        this.showToast(data.message);
        this.closeModals();
        await this.loadGlobalState();
        await this.loadConversationsList();
        if (data.job) this.trackJob(data.job);
      }
    } finally {
      btn.disabled = false;
      btn.textContent = "⬇️ Загрузить и оценить набор";
    }
  },

  handleJsonFileSelect(event) {
    const file = event.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (e) => {
      document.getElementById("jsonPasteInput").value = e.target.result || "";
    };
    reader.readAsText(file);
  },

  async submitJsonUpload() {
    const rawText = document.getElementById("jsonPasteInput").value.trim();
    if (!rawText) {
      this.showToast("Выберите файл или вставьте JSON");
      return;
    }
    let parsed;
    try {
      parsed = JSON.parse(rawText);
    } catch (e) {
      this.showToast("Ошибка синтаксиса JSON: " + e.message);
      return;
    }

    const res = await fetch("/api/conversations/upload", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ payload: parsed, group_id: "UPLOAD" }),
    });
    const data = await res.json();
    if (!data.isSuccess) {
      this.showToast(data.error || "Ошибка загрузки", "error");
      return;
    }

    this.showToast(data.message);
    this.closeModals();
    await this.loadGlobalState();
    await this.loadConversationsList();
    if (data.first_conversation_id) {
      await this.selectConversation(data.first_conversation_id);
    }
    if (data.job) this.trackJob(data.job);
  },

  async submitAppealFromModal() {
    const critId = document.getElementById("appealCriterionSelect").value;
    const author = document.getElementById("appealAuthorInput").value.trim();
    const comment = document.getElementById("appealCommentInput").value.trim();
    if (!comment) {
      this.showToast("Введите комментарий к апелляции");
      return;
    }

    const res = await fetch("/api/appeals/submit", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        conversation_id: this.state.currentConversationId,
        criterion_id: critId,
        comment,
        author,
      }),
    });
    const data = await res.json();
    if (!data.isSuccess) {
      this.showToast(data.error || "Ошибка подачи апелляции");
      return;
    }

    this.showToast(data.message);
    this.closeModals();
    this.state.openInlineReviewCritId = critId;
    await this.loadGlobalState();
    await this.loadConversationsList();
    await this.selectConversation(this.state.currentConversationId);
  },
};

window.addEventListener("DOMContentLoaded", () => App.init());
