/**
 * ComfyUI LLM API - multi-provider frontend.
 *
 * The server bakes one static list into the `model` and `reasoning_effort`
 * combos because ComfyUI only calls INPUT_TYPES() once. This extension narrows
 * both lists to the selected provider and refreshes the model list live from
 * /comfyui_llm_api/models.
 */

import { app } from "../../../scripts/app.js";
import { api } from "../../../scripts/api.js";

const EXTENSION_NAME = "LLM.API.MultiProvider";
const NODE_CLASS = "LLMAPITextProcessor";

const ROUTE_PROVIDERS = "/comfyui_llm_api/providers";
const ROUTE_MODELS = "/comfyui_llm_api/models";

const API_TTL_MS = 5 * 60 * 1000;
const API_FAIL_TTL_MS = 45 * 1000;

/**
 * Mirrors providers.py. Used only until /providers answers, and if the route is
 * unreachable - the UI must never render an empty dropdown.
 */
const BUILTIN_REGISTRY = {
    defaultProvider: "cometapi",
    providers: [
        {
            id: "cometapi",
            label: "CometAPI (cometapi.com)",
            baseUrl: "https://api.cometapi.com/v1",
            keyEnv: "COMETAPI_API_KEY",
            defaultModel: "grok-4.7",
            models: [
                "grok-4.7",
                "grok-build-0.1",
                "deepseek-v4.1-flash",
                "deepseek-v4-pro",
                "gpt-6-astra",
                "claude-opus-4-8",
            ],
            efforts: ["default", "minimal", "low", "medium", "high", "xhigh", "max"],
        },
        {
            id: "grokified",
            label: "Grokified API (grokified.com)",
            baseUrl: "https://api.grokified.com/v1",
            keyEnv: "GROKIFIED_API_KEY",
            defaultModel: "grok-4.7",
            models: ["grok-4.7", "grok-4.6", "grok-4.5", "grok-4.3"],
            efforts: ["default", "low", "medium", "high", "xhigh"],
        },
        {
            id: "custom",
            label: "Custom / self-hosted (OpenAI-compatible)",
            baseUrl: "http://127.0.0.1:1234/v1",
            keyEnv: "LLM_API_KEY",
            defaultModel: "local-model",
            models: ["local-model"],
            efforts: ["default", "minimal", "low", "medium", "high", "xhigh", "max"],
        },
    ],
};

// --------------------------------------------------------------------------- //
// small helpers
// --------------------------------------------------------------------------- //
const findWidget = (node, name) => (node?.widgets || []).find((w) => w.name === name);

function unique(list) {
    return [...new Set((list || []).map((v) => String(v ?? "").trim()).filter(Boolean))];
}

async function getJson(url) {
    if (api?.fetchApi) {
        const response = await api.fetchApi(url, { cache: "no-store" });
        if (!response.ok) throw new Error(`HTTP ${response.status} for ${url}`);
        return response.json();
    }
    const response = await fetch(url, { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status} for ${url}`);
    return response.json();
}

function indexRegistry(payload) {
    const providers = payload?.providers?.length ? payload.providers : BUILTIN_REGISTRY.providers;
    const byId = new Map();
    const byLabel = new Map();
    for (const provider of providers) {
        const entry = {
            id: provider.id,
            label: provider.label || provider.id,
            baseUrl: provider.baseUrl || "",
            keyEnv: provider.keyEnv || "",
            keyHint: provider.keyHint || "",
            defaultModel: provider.defaultModel || "",
            models: unique(provider.models),
            efforts: unique(provider.efforts),
            docsUrl: provider.docsUrl || "",
            notes: provider.notes || "",
        };
        byId.set(entry.id, entry);
        byLabel.set(entry.label, entry);
    }
    const defaultProvider = byId.get(payload?.defaultProvider) || [...byId.values()][0] || null;
    return { providers: [...byId.values()], byId, byLabel, defaultProvider };
}

function resolveEntry(registry, value) {
    const text = String(value ?? "").trim();
    if (!text) return registry.defaultProvider;
    return registry.byId.get(text) || registry.byLabel.get(text) || registry.defaultProvider;
}

/** Replace a combo's option list, keeping the current value when still valid. */
function setComboValues(widget, values, fallback) {
    const list = unique(values);
    if (!widget || list.length === 0) return false;
    widget.options = widget.options || {};
    const previous = widget.options.values;
    const changed = !Array.isArray(previous) || previous.length !== list.length || previous.some((v, i) => v !== list[i]);
    widget.options.values = list;
    if (!list.includes(widget.value)) {
        widget.value = fallback && list.includes(fallback) ? fallback : list[0];
        return true;
    }
    return changed;
}

// --------------------------------------------------------------------------- //
// registry + model cache
// --------------------------------------------------------------------------- //
let registryPromise = null;
const modelCache = new Map();

function loadRegistry(force = false) {
    if (force || !registryPromise) {
        registryPromise = getJson(ROUTE_PROVIDERS)
            .then(indexRegistry)
            .catch((error) => {
                console.warn(`[${EXTENSION_NAME}] /providers unavailable, using the built-in list:`, error);
                return indexRegistry(BUILTIN_REGISTRY);
            });
    }
    return registryPromise;
}

async function loadModels(entry, force = false) {
    const cached = modelCache.get(entry.id);
    if (!force && cached) {
        const ttl = cached.error ? API_FAIL_TTL_MS : API_TTL_MS;
        if (Date.now() - cached.ts < ttl) return cached;
    }

    let record;
    try {
        const url = `${ROUTE_MODELS}?provider=${encodeURIComponent(entry.id)}${force ? "&refresh=1" : ""}`;
        const payload = await getJson(url);
        record = {
            models: unique(payload?.models).length ? unique(payload.models) : entry.models,
            error: payload?.error || null,
            ts: Date.now(),
        };
    } catch (error) {
        record = { models: entry.models, error: String(error?.message || error), ts: Date.now() };
    }
    modelCache.set(entry.id, record);
    return record;
}

// --------------------------------------------------------------------------- //
// per-node reconcile
// --------------------------------------------------------------------------- //
function ensureStatusWidget(node) {
    if (node.__llmApiStatusEl) return node.__llmApiStatusEl;

    const el = document.createElement("div");
    el.style.cssText =
        "font:11px/1.45 ui-monospace,Menlo,Consolas,monospace;padding:3px 6px;" +
        "opacity:.85;white-space:pre-wrap;word-break:break-word;user-select:text;";
    el.textContent = "LLM API: loading providers...";

    try {
        const widget = node.addDOMWidget("provider_status", "info", el, {
            serialize: false,
            hideOnZoom: false,
            getValue: () => "",
            setValue: () => {},
        });
        if (widget) widget.serialize = false;
    } catch (error) {
        console.warn(`[${EXTENSION_NAME}] addDOMWidget unavailable:`, error);
    }

    node.__llmApiStatusEl = el;
    return el;
}

function setStatus(node, text) {
    const el = ensureStatusWidget(node);
    if (el) el.textContent = text;
    node.__llmApiStatusText = text;
}

function ensureRefreshButton(node) {
    if (node.__llmApiRefreshButton) return;

    const providerWidget = () => findWidget(node, "provider");
    const onClick = async () => {
        const registry = await loadRegistry();
        const entry = resolveEntry(registry, providerWidget()?.value);
        modelCache.delete(entry.id);
        await applyProvider(node, entry.id, { force: true });
        app.graph?.setDirtyCanvas(true, true);
    };

    try {
        const button = node.addWidget("button", "🔄 Refresh model list", null, () => {
            onClick().catch((error) => console.warn(`[${EXTENSION_NAME}] refresh failed:`, error));
        });
        if (button) {
            button.serialize = false;
            button.serializeValue = () => undefined;
        }
        node.__llmApiRefreshButton = button;
    } catch (error) {
        console.warn(`[${EXTENSION_NAME}] addWidget("button") unavailable:`, error);
    }
}

/**
 * Point every provider-dependent widget at `providerValue`.
 * Safe to call repeatedly; it is also the workflow-load path.
 */
async function applyProvider(node, providerValue, options = {}) {
    if (node.comfyClass !== NODE_CLASS) return null;

    const registry = await loadRegistry(options.forceRegistry);
    const entry = resolveEntry(registry, providerValue);
    if (!entry) return null;

    // keep the combo itself on a canonical label
    const providerWidget = findWidget(node, "provider");
    if (providerWidget && providerWidget.value !== entry.label) providerWidget.value = entry.label;

    const record = await loadModels(entry, options.force);
    const models = unique([entry.defaultModel, ...record.models]);

    setComboValues(findWidget(node, "model"), models, entry.defaultModel);
    setComboValues(findWidget(node, "reasoning_effort"), entry.efforts, "high");

    // base_url: only overwrite a value the user has not customised
    const baseWidget = findWidget(node, "base_url");
    if (baseWidget) {
        const known = new Set(registry.providers.map((p) => p.baseUrl));
        const current = String(baseWidget.value ?? "").trim();
        if (!current || known.has(current)) baseWidget.value = entry.baseUrl;
        baseWidget.options = baseWidget.options || {};
        baseWidget.options.placeholder = entry.baseUrl;
    }

    const keyWidget = findWidget(node, "api_key");
    if (keyWidget) {
        keyWidget.options = keyWidget.options || {};
        keyWidget.options.placeholder = entry.keyEnv
            ? `empty = $${entry.keyEnv}`
            : entry.keyHint || "optional";
    }

    const modelWidget = findWidget(node, "model");
    if (modelWidget) {
        modelWidget.options = modelWidget.options || {};
        modelWidget.options.placeholder = entry.defaultModel;
    }

    const lines = [
        `${entry.label}`,
        `${models.length} model${models.length === 1 ? "" : "s"} · key: ${entry.keyEnv || "(none)"}`,
    ];
    if (record.error) lines.push(`⚠ ${String(record.error).slice(0, 200)}`);
    setStatus(node, lines.join("\n"));

    node.properties = node.properties || {};
    node.properties["llm_api_provider"] = entry.id;

    node.setDirtyCanvas?.(true, true);
    return entry;
}

// --------------------------------------------------------------------------- //
// extension
// --------------------------------------------------------------------------- //
app.registerExtension({
    name: EXTENSION_NAME,

    async setup() {
        // warm the caches so the first node opens already populated
        const registry = await loadRegistry();
        await Promise.all(registry.providers.map((entry) => loadModels(entry)));
    },

    async nodeCreated(node) {
        if (node.comfyClass !== NODE_CLASS) return;

        ensureRefreshButton(node);

        const providerWidget = findWidget(node, "provider");
        if (providerWidget) {
            const original = providerWidget.callback;
            providerWidget.callback = function (value, ...rest) {
                const result = original?.apply(this, [value, ...rest]);
                applyProvider(node, value).catch((error) =>
                    console.warn(`[${EXTENSION_NAME}] provider switch failed:`, error),
                );
                return result;
            };
        }

        // nodeCreated runs before a loaded workflow restores widget values, so
        // defer the first pass; loadedGraphNode() covers the load case again.
        setTimeout(() => {
            applyProvider(node, providerWidget?.value).catch((error) =>
                console.warn(`[${EXTENSION_NAME}] initial sync failed:`, error),
            );
        }, 0);
    },

    async loadedGraphNode(node) {
        if (node.comfyClass !== NODE_CLASS) return;
        const providerWidget = findWidget(node, "provider");
        await applyProvider(node, providerWidget?.value);
        ensureRefreshButton(node);
    },
});

// exposed for tests and for the browser console
window.__LLM_API__ = {
    EXTENSION_NAME,
    NODE_CLASS,
    BUILTIN_REGISTRY,
    loadRegistry,
    loadModels,
    applyProvider,
    setComboValues,
    findWidget,
    modelCache,
};
