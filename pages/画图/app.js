const bridge = window.AstrBotPluginPage;
const $ = (id) => document.getElementById(id);

function unwrap(result) {
  if (result && typeof result === "object") {
    if (result.ok === false) throw new Error(result.error?.message || result.message || "请求失败");
    if (Object.prototype.hasOwnProperty.call(result, "data")) return result.data;
  }
  return result ?? {};
}

const apiGet = async (endpoint) => unwrap(await bridge.apiGet(endpoint, {}));
const apiPost = async (endpoint, body) => unwrap(await bridge.apiPost(endpoint, body || {}));

function setMsg(text, kind = "") {
  const box = $("msg");
  box.textContent = text || "";
  box.className = "msg" + (kind ? " " + kind : "");
}

function setSaveMsg(text, kind = "") {
  const box = $("saveMsg");
  box.textContent = text || "";
  box.className = "msg" + (kind ? " " + kind : "");
}

function escapeText(value) {
  return String(value).replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
}

function showLatest(image) {
  if (!image) return;
  $("latestPanel").hidden = false;
  $("latestImg").src = image.data;
  $("latestPrompt").textContent = image.prompt || "";
}

function renderGrid(images) {
  const grid = $("grid");
  $("count").textContent = images.length ? `最近 ${images.length} 张` : "";
  if (!images.length) {
    grid.innerHTML = `<div class="empty">还没有作品，上面写一段描述点「生成」。</div>`;
    return;
  }
  grid.innerHTML = images
    .map(
      (img, index) => `
      <div class="item" data-index="${index}">
        <img src="${img.data}" alt="" loading="lazy" />
        <span>${escapeText(img.prompt || "（无描述）")}</span>
      </div>`
    )
    .join("");
  grid.querySelectorAll(".item").forEach((node) => {
    node.addEventListener("click", () => showLatest(images[Number(node.dataset.index)]));
  });
}

function renderState(state) {
  if (state.model) $("model").textContent = "模型：" + state.model;
  const images = state.images || [];
  renderGrid(images);
  if (images.length) showLatest(images[0]);
}

async function generate() {
  const prompt = $("prompt").value.trim();
  if (!prompt) {
    setMsg("先写点画面描述", "err");
    return;
  }
  const btn = $("btnGo");
  btn.disabled = true;
  setMsg("画着呢，大概 15–30 秒…");
  try {
    const data = await apiPost("page/generate", { prompt, size: $("size").value.trim() });
    if (data.image) showLatest(data.image);
    if (data.state) {
      $("model").textContent = data.state.model ? "模型：" + data.state.model : $("model").textContent;
      renderGrid(data.state.images || []);
    }
    setMsg(`画好了，用时 ${((data.elapsed_ms || 0) / 1000).toFixed(1)}s`);
  } catch (error) {
    setMsg(error.message, "err");
  } finally {
    btn.disabled = false;
  }
}

// ------------------------------------------------------------------ 设置

function renderProvider(info) {
  const card = $("providerCard");
  if (!info || info.ok === false) {
    card.innerHTML = `<span>读取失败：${escapeText(info?.error || "未知错误")}</span>`;
    return;
  }
  card.innerHTML = `
    <span>提供方：<b>${escapeText(info.provider || "—")}</b></span>
    <span>地址：<b>${escapeText(info.base_url || "—")}</b></span>
    <span>当前模型：<b>${escapeText(info.model || "—")}</b></span>
    <span>默认尺寸：<b>${escapeText(info.size || "—")}</b></span>
    <span>API Key：<b>${escapeText(info.api_key || "—")}</b></span>`;
}

function fillConfig(data) {
  const values = data.values || {};
  const options = data.options || {};
  $("cfgEnabled").checked = Boolean(values.enabled);
  $("cfgWatermark").checked = Boolean(values.watermark_off);
  $("cfgTimeout").value = values.timeout ?? 120;
  $("cfgMaxPrompt").value = values.max_prompt_chars ?? 600;
  $("cfgNegative").value = values.negative_prompt || "";
  $("cfgStyle").value = values.style || "";
  $("cfgNegative").placeholder = options.defaults?.negative_prompt || "";
  $("cfgProviders").value = values.providers_file || "";
  renderProvider(options.provider);
}

function collectConfig() {
  return {
    enabled: $("cfgEnabled").checked,
    watermark_off: $("cfgWatermark").checked,
    timeout: Number($("cfgTimeout").value || 120),
    max_prompt_chars: Number($("cfgMaxPrompt").value || 600),
    negative_prompt: $("cfgNegative").value,
    style: $("cfgStyle").value,
    providers_file: $("cfgProviders").value.trim(),
  };
}

async function loadConfig() {
  fillConfig(await apiGet("page/config"));
}

async function saveConfig() {
  const btn = $("btnSave");
  btn.disabled = true;
  setSaveMsg("保存中…");
  try {
    const data = await apiPost("page/save", collectConfig());
    const newPath = data?.values?.providers_file || "";
    let note = "";
    if (providersDirty) {
      if (newPath && newPath !== loadedProvidersPath) {
        note = "；provider 文件路径变了，提供方表单已按新文件重载，请确认后再保存一次";
      } else {
        await saveProviders();
      }
    }
    await Promise.all([loadConfig(), loadProviders()]);
    setSaveMsg("已保存，已生效" + note, "ok");
  } catch (error) {
    setSaveMsg(error.message, "err");
  } finally {
    btn.disabled = false;
  }
}

// ------------------------------------------------------------------ 提供方（providers.json）

const PROVIDER_TYPES = [
  {
    value: "siliconflow",
    label: "硅基流动",
    url: "https://api.siliconflow.cn/v1",
    hint: "例：Tongyi-MAI/Z-Image、Kwai-Kolors/Kolors；支持 negative_prompt 字段",
  },
  {
    value: "openai",
    label: "OpenAI 官方",
    url: "https://api.openai.com/v1",
    hint: "例：gpt-image-1、dall-e-3；不支持 negative_prompt，会拼进提示词",
  },
  {
    value: "dashscope",
    label: "阿里云百炼",
    url: "https://dashscope.aliyuncs.com/compatible-mode/v1",
    hint: "Compatible Mode 入口；例：wanx2.1-t2i-turbo、qwen-image",
  },
  {
    value: "zhipu",
    label: "智谱",
    url: "https://open.bigmodel.cn/api/paas/v4",
    hint: "例：cogview-4、cogview-3-flash",
  },
  {
    value: "volcengine",
    label: "火山方舟",
    url: "https://ark.cn-beijing.volces.com/api/v3",
    hint: "例：doubao-seedream-4-0-250828",
  },
  {
    value: "custom",
    label: "自定义 / 其他兼容接口",
    url: "",
    hint: "填任何兼容 OpenAI /images/generations 的地址",
  },
];

let providerState = {
  providers: [],
  default_model_id: "",
  fallback_on_error: false,
};
let loadedProvidersPath = "";
let providersDirty = false;

function providerTypeMeta(value) {
  return PROVIDER_TYPES.find((item) => item.value === value) || PROVIDER_TYPES[0];
}

function typeOptions(selected) {
  const current = selected || "siliconflow";
  return PROVIDER_TYPES.map(
    (item) =>
      `<option value="${item.value}"${item.value === current ? " selected" : ""}>${escapeText(item.label)}</option>`
  ).join("");
}

function cardMsg(card, text, kind = "") {
  const box = card.querySelector(".prov-msg");
  if (!box) return;
  box.textContent = text || "";
  box.className = "prov-msg msg" + (kind ? " " + kind : "");
}

function modelRowTemplate(model) {
  const item = model || {};
  return `<div class="model-row">
      <input class="m-model" value="${escapeText(item.model || "")}" placeholder="模型 ID，例如 Tongyi-MAI/Z-Image" />
      <input class="m-size" value="${escapeText(item.default_size || "")}" placeholder="尺寸，可空" />
      <label class="check"><input type="checkbox" class="m-enabled" ${item.enabled === false ? "" : "checked"} /> 启用</label>
      <button type="button" class="ghost danger m-del" title="删除这个模型">×</button>
    </div>`;
}

function providerTemplate(provider) {
  const item = provider || {};
  const type = item.provider_type || "siliconflow";
  const meta = providerTypeMeta(type);
  const hasKey = Boolean(item.api_key_masked);
  const keyBlock = hasKey
    ? `<span class="key-chip">已配置（${escapeText(item.api_key_masked)}）</span>
            <button type="button" class="ghost key-replace">替换</button>
            <button type="button" class="ghost danger key-clear">清除</button>`
    : "";
  const models = (item.models && item.models.length ? item.models : [{}]).map(modelRowTemplate).join("");
  return `<article class="prov" data-id="${escapeText(item.id || "")}">
      <div class="prov-head">
        <input class="prov-name" value="${escapeText(item.name || "")}" placeholder="提供方名称，例如 siliconflow" />
        <select class="prov-type">${typeOptions(type)}</select>
        <label class="check"><input type="checkbox" class="prov-enabled" ${item.enabled === false ? "" : "checked"} /> 启用</label>
        <button type="button" class="ghost prov-test">测试并拉取模型</button>
        <button type="button" class="ghost danger prov-del">删除</button>
      </div>
      <p class="hint prov-tip">${escapeText(meta.hint)}</p>
      <div class="prov-msg msg"></div>
      <div class="grid2">
        <label class="field">
          <span>Base URL</span>
          <input class="prov-url" value="${escapeText(item.base_url || "")}" placeholder="${escapeText(meta.url || "https://…/v1")}" />
        </label>
        <label class="field">
          <span>API Key（完整值只存在服务器上，页面不回显）</span>
          <div class="key-row">
            <input class="prov-key" type="password" autocomplete="off" ${hasKey ? "hidden" : ""} placeholder="${escapeText(hasKey ? "贴新的 key 覆盖" : "sk-…（填完点保存）")}" />
            <button type="button" class="ghost key-save" ${hasKey ? "hidden" : ""}>保存 key</button>
            ${keyBlock}
          </div>
        </label>
      </div>
      <div class="models">
        <div class="models-head"><span>模型</span><button type="button" class="ghost model-add">＋ 加一个模型</button></div>
        <div class="model-rows">${models}</div>
        <div class="pick" hidden>
          <input class="pick-filter" placeholder="筛选模型名，例如 image / kolors" />
          <div class="pick-chips"></div>
        </div>
      </div>
    </article>`;
}

function renderProviders(payload) {
  providerState = payload || providerState;
  const providers = providerState.providers || [];
  $("providerList").innerHTML = providers.length
    ? providers.map(providerTemplate).join("")
    : `<div class="empty">还没有提供方，点右上角「＋ 新增提供方」。</div>`;
  $("cfgFallback").checked = Boolean(providerState.fallback_on_error);
  fillDefaultModel();
}

function fillDefaultModel() {
  const select = $("cfgDefaultModel");
  const rows = [];
  (providerState.providers || []).forEach((provider) => {
    if (provider.enabled === false) return;
    (provider.models || []).forEach((model) => {
      if (model.enabled === false || !model.model) return;
      const value = `${provider.id}::${model.model}`;
      rows.push(
        `<option value="${escapeText(value)}">${escapeText(
          `${provider.name || provider.id} · ${model.model}`
        )}</option>`
      );
    });
  });
  select.innerHTML = rows.length ? rows.join("") : `<option value="">（还没有可用模型）</option>`;
  const wanted = providerState.default_model_id || "";
  select.value = wanted;
  if (select.value !== wanted && rows.length) select.value = "";
}

function collectProviders() {
  const providers = Array.from(document.querySelectorAll("#providerList .prov")).map((card) => ({
    id: card.dataset.id || "",
    name: card.querySelector(".prov-name").value.trim(),
    provider_type: card.querySelector(".prov-type").value,
    base_url: card.querySelector(".prov-url").value.trim(),
    api_key: card.querySelector(".prov-key").value.trim(),
    enabled: card.querySelector(".prov-enabled").checked,
    models: Array.from(card.querySelectorAll(".model-row")).map((row) => ({
      model: row.querySelector(".m-model").value.trim(),
      default_size: row.querySelector(".m-size").value.trim(),
      enabled: row.querySelector(".m-enabled").checked,
    })),
  }));
  return {
    providers,
    default_model_id: $("cfgDefaultModel").value,
    fallback_on_error: $("cfgFallback").checked,
  };
}

async function loadProviders() {
  const payload = await apiGet("page/providers");
  loadedProvidersPath = payload.path || "";
  renderProviders(payload);
  providersDirty = false;
}

async function saveProviders() {
  const data = await apiPost("page/providers/save", collectProviders());
  renderProviders(data);
  providersDirty = false;
}

function renderChips(card, keyword) {
  const box = card.querySelector(".pick-chips");
  if (!box) return;
  const models = card._models || [];
  const kw = String(keyword || "").trim().toLowerCase();
  const matched = kw ? models.filter((item) => item.toLowerCase().includes(kw)) : models;
  const existing = new Set(
    Array.from(card.querySelectorAll(".m-model")).map((input) => input.value.trim())
  );
  const shown = matched.slice(0, 60);
  box.innerHTML = shown.length
    ? shown
        .map(
          (name) =>
            `<button type="button" class="chip${existing.has(name) ? " on" : ""}" data-model="${escapeText(name)}">${escapeText(name)}</button>`
        )
        .join("")
    : `<span class="hint">没有匹配的模型</span>`;
}

function armConfirm(button, label, action) {
  if (!button) return;
  if (button.dataset.armed === "1") {
    button.dataset.armed = "";
    button.textContent = label;
    button.classList.remove("armed");
    action();
    return;
  }
  button.dataset.armed = "1";
  button.textContent = "再点一次";
  button.classList.add("armed");
  window.setTimeout(function () {
    if (button.dataset.armed === "1") {
      button.dataset.armed = "";
      button.textContent = label;
      button.classList.remove("armed");
    }
  }, 4000);
}

async function saveKey(card, button) {
  const input = card.querySelector(".prov-key");
  const value = input ? input.value.trim() : "";
  if (!value) {
    cardMsg(card, "先填入 API Key", "err");
    return;
  }
  if (!card.dataset.id) {
    cardMsg(card, "新提供方：先点底部「保存设置」创建", "err");
    return;
  }
  if (button) button.disabled = true;
  cardMsg(card, "保存中…");
  try {
    const data = await apiPost("page/providers/set-key", {
      id: card.dataset.id,
      api_key: value,
    });
    const providerId = card.dataset.id;
    renderProviders(data);
    providersDirty = false;
    const fresh = document.querySelector(`#providerList .prov[data-id="${providerId}"]`);
    if (fresh) cardMsg(fresh, "key 已保存，可以点「测试并拉取模型」验证", "ok");
  } catch (error) {
    cardMsg(card, error.message, "err");
  } finally {
    if (button) button.disabled = false;
  }
}

function resetKeyRow(card) {
  const row = card.querySelector(".key-row");
  const input = row?.querySelector(".prov-key");
  if (!row || !input) return;
  row.querySelector(".key-chip")?.remove();
  row.querySelector(".key-replace")?.remove();
  row.querySelector(".key-clear")?.remove();
  row.insertAdjacentHTML("afterbegin", `<span class="key-chip empty">未配置</span>`);
  input.hidden = false;
  input.value = "";
  input.placeholder = "已清除；填一把新的就能恢复";
}

async function deleteProvider(card) {
  const providerId = card.dataset.id || "";
  if (!providerId) {
    card.remove();
    providersDirty = true;
    return;
  }
  cardMsg(card, "删除中…");
  try {
    const data = await apiPost("page/providers/delete", { id: providerId });
    renderProviders(data);
    providersDirty = false;
    await loadConfig();
    if (!(data.providers || []).length) {
      setSaveMsg("提供方已全部删除：配图功能暂时不可用，点「＋ 新增提供方」可以重新配置", "ok");
    }
  } catch (error) {
    cardMsg(card, error.message, "err");
  }
}

async function clearKey(card) {
  const providerId = card.dataset.id || "";
  if (!providerId) {
    resetKeyRow(card);
    cardMsg(card, "已清空输入框（这个提供方还没保存过）", "ok");
    return;
  }
  const btn = card.querySelector(".key-clear");
  if (btn) btn.disabled = true;
  cardMsg(card, "清除中…");
  try {
    const data = await apiPost("page/providers/clear-key", { id: providerId });
    renderProviders(data);
    providersDirty = false;
    const fresh = document.querySelector(`#providerList .prov[data-id="${providerId}"]`);
    if (fresh) cardMsg(fresh, "已清除：填回 key 之前不参与生成", "ok");
  } catch (error) {
    cardMsg(card, error.message, "err");
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function testCard(card) {
  const btn = card.querySelector(".prov-test");
  btn.disabled = true;
  cardMsg(card, "拉取中…");
  try {
    const result = await apiPost("page/providers/test", {
      id: card.dataset.id || "",
      base_url: card.querySelector(".prov-url").value.trim(),
      api_key: card.querySelector(".prov-key").value.trim(),
    });
    if (!result.ok) {
      cardMsg(card, result.error || "连不上", "err");
      return;
    }
    const models = result.models || [];
    card._models = models;
    cardMsg(card, `连上了：HTTP ${result.status}，${models.length} 个模型，点下面的名字加进来`, "ok");
    const pick = card.querySelector(".pick");
    if (pick) pick.hidden = false;
    renderChips(card, card.querySelector(".pick-filter")?.value || "");
  } catch (error) {
    cardMsg(card, error.message, "err");
  } finally {
    btn.disabled = false;
  }
}

// ------------------------------------------------------------------ 初始化

function switchTab(name) {
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.classList.toggle("active", tab.dataset.tab === name);
  });
  $("tab-overview").hidden = name !== "overview";
  $("tab-settings").hidden = name !== "settings";
  if (name === "settings") {
    Promise.all([loadConfig(), loadProviders()]).catch((error) => setSaveMsg(error.message, "err"));
  }
}

function bindProviderEditor() {
  $("btnAddProvider").addEventListener("click", () => {
    $("providerList").insertAdjacentHTML("beforeend", providerTemplate({}));
    providersDirty = true;
  });

  $("providerList").addEventListener("click", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLElement)) return;
    const card = target.closest(".prov");
    if (target.classList.contains("prov-del")) {
      if (!card) return;
      armConfirm(target, "删除", () => deleteProvider(card));
    } else if (target.classList.contains("prov-test")) {
      if (card) testCard(card);
    } else if (target.classList.contains("chip")) {
      const name = target.dataset.model || "";
      const rows = card?.querySelector(".model-rows");
      if (!rows || !name) return;
      const existing = Array.from(card.querySelectorAll(".m-model")).map((input) => input.value.trim());
      if (existing.includes(name)) return;
      rows.insertAdjacentHTML("beforeend", modelRowTemplate({ model: name }));
      providersDirty = true;
      renderChips(card, card.querySelector(".pick-filter")?.value || "");
    } else if (target.classList.contains("model-add")) {
      const rows = target.closest(".models")?.querySelector(".model-rows");
      if (rows) rows.insertAdjacentHTML("beforeend", modelRowTemplate({}));
      providersDirty = true;
    } else if (target.classList.contains("m-del")) {
      const row = target.closest(".model-row");
      if (row) row.remove();
      providersDirty = true;
    } else if (target.classList.contains("key-replace")) {
      const row = target.closest(".key-row");
      const input = row?.querySelector(".prov-key");
      if (!row || !input) return;
      row.querySelector(".key-chip")?.remove();
      row.querySelector(".key-replace")?.remove();
      row.querySelector(".key-clear")?.remove();
      const saveBtn = row.querySelector(".key-save");
      if (saveBtn) saveBtn.hidden = false;
      input.hidden = false;
      input.focus();
    } else if (target.classList.contains("key-clear")) {
      if (!card) return;
      armConfirm(target, "清除", () => clearKey(card));
    } else if (target.classList.contains("key-save")) {
      if (!card) return;
      saveKey(card, target);
    }
  });

  $("providerList").addEventListener("keydown", (event) => {
    const target = event.target;
    if (event.key !== "Enter") return;
    if (!(target instanceof HTMLElement) || !target.classList.contains("prov-key")) return;
    event.preventDefault();
    const card = target.closest(".prov");
    if (card) saveKey(card, card.querySelector(".key-save"));
  });

  $("providerList").addEventListener("input", (event) => {
    providersDirty = true;
    const target = event.target;
    if (target instanceof HTMLElement && target.classList.contains("pick-filter")) {
      const card = target.closest(".prov");
      if (card) renderChips(card, target.value);
    }
  });

  $("providerList").addEventListener("change", (event) => {
    providersDirty = true;
    const target = event.target;
    if (!(target instanceof HTMLElement) || !target.classList.contains("prov-type")) return;
    const card = target.closest(".prov");
    if (!card) return;
    const meta = providerTypeMeta(target.value);
    const tip = card.querySelector(".prov-tip");
    if (tip) tip.textContent = meta.hint;
    const url = card.querySelector(".prov-url");
    const known = PROVIDER_TYPES.map((item) => item.url).filter(Boolean);
    if (url && (!url.value.trim() || known.includes(url.value.trim()))) {
      url.value = meta.url || "";
      url.placeholder = meta.url || "https://…/v1";
    }
  });

  ["cfgFallback", "cfgDefaultModel"].forEach((id) => {
    $(id).addEventListener("input", () => {
      providersDirty = true;
    });
    $(id).addEventListener("change", () => {
      providersDirty = true;
    });
  });
}

async function init() {
  if (!bridge) {
    document.body.innerHTML = '<div class="empty">没有检测到 AstrBot 页面桥接，请从 WebUI 插件页面进入。</div>';
    return;
  }
  try {
    await bridge.ready();
  } catch (error) {
    setMsg(error.message || "页面初始化失败", "err");
    return;
  }
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.addEventListener("click", () => switchTab(tab.dataset.tab));
  });
  $("btnGo").addEventListener("click", generate);
  $("prompt").addEventListener("keydown", (event) => {
    if ((event.metaKey || event.ctrlKey) && event.key === "Enter") generate();
  });
  $("btnSave").addEventListener("click", saveConfig);
  bindProviderEditor();
  try {
    renderState(await apiGet("page/state"));
  } catch (error) {
    setMsg(error.message, "err");
  }
}

init();
