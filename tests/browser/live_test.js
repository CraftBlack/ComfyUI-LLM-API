/**
 * End-to-end browser test against a REAL ComfyUI instance.
 *
 * It loads ComfyUI, injects a workflow that contains the multi-provider node,
 * and checks that web/js/llm_api.js actually re-points every provider-dependent
 * combo - the one thing a Python-only test cannot prove.
 *
 * Usage:
 *     node tests/browser/live_test.js                       # default http://127.0.0.1:8189
 *     COMFY_URL=http://127.0.0.1:8188 node tests/browser/live_test.js
 *     SHOT=docs/node.png node tests/browser/live_test.js    # also save a screenshot
 */

const path = require("path");

const BASE = (process.env.COMFY_URL || "http://127.0.0.1:8189").replace(/\/$/, "");
const SHOT = process.env.SHOT || "";

function loadPlaywright() {
    const candidates = [
        process.env.PLAYWRIGHT_PATH,
        "playwright",
        "C:/Users/User/AppData/Roaming/npm/node_modules/omniroute/node_modules/playwright",
        path.join(process.env.APPDATA || "", "npm/node_modules/playwright"),
    ].filter(Boolean);
    for (const candidate of candidates) {
        try {
            return require(candidate);
        } catch (_) {
            /* try the next candidate */
        }
    }
    throw new Error("Playwright not found. `npm i -g playwright` or set PLAYWRIGHT_PATH.");
}

const { chromium } = loadPlaywright();

const PASSED = [];
const FAILED = [];
const check = (label, ok, detail = "") => {
    if (ok) PASSED.push(label);
    else FAILED.push(`${label}${detail ? ` :: ${detail}` : ""}`);
};
const eq = (label, actual, expected) =>
    check(label, JSON.stringify(actual) === JSON.stringify(expected), `expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`);

const WORKFLOW = {
    last_node_id: 1,
    last_link_id: 0,
    nodes: [
        {
            id: 1,
            type: "LLMAPITextProcessor",
            pos: [120, 120],
            size: [420, 700],
            flags: {},
            order: 0,
            mode: 0,
            inputs: [],
            outputs: [
                { name: "RESPONSE", type: "STRING", links: null },
                { name: "REASONING", type: "STRING", links: null },
                { name: "PERF", type: "STRING", links: null },
                { name: "USAGE_JSON", type: "STRING", links: null },
            ],
            properties: {},
            // provider=Grokified on purpose: the server-side combo default is
            // CometAPI, so a successful narrow proves the restore path ran.
            widgets_values: [
                "Grokified API (grokified.com)",
                "grok-4.6",
                "none",
                "describe this",
                1024,
                0.5,
                0.9,
                "xhigh",
                42,
                120,
            ],
        },
    ],
    links: [],
    groups: [],
    config: {},
    extra: {},
    version: 0.4,
};

async function launch() {
    try {
        return await chromium.launch({ headless: true });
    } catch (_) {
        console.log("[info] bundled Chromium unavailable, falling back to installed Chrome");
        return await chromium.launch({ headless: true, channel: "chrome" });
    }
}

(async () => {
    const browser = await launch();
    const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });

    const consoleErrors = [];
    const notFound = [];
    page.on("console", (msg) => {
        if (msg.type() === "error") consoleErrors.push(msg.text());
    });
    page.on("pageerror", (error) => consoleErrors.push(`pageerror: ${error.message}`));
    page.on("response", (response) => {
        if (response.status() === 404) notFound.push(response.url());
    });

    try {
        console.log(`== loading ${BASE} ==`);
        await page.goto(BASE, { waitUntil: "domcontentloaded", timeout: 90_000 });
        await page.waitForFunction(() => window.app?.graph && window.LiteGraph, null, { timeout: 90_000 });
        await page.waitForFunction(() => !!window.__LLM_API__, null, { timeout: 60_000 });
        check("extension script loaded", true);

        // --------------------------------------------------------------- //
        console.log("== HTTP routes ==");
        const providers = await page.evaluate(async () => {
            const r = await fetch("/comfyui_llm_api/providers");
            return { status: r.status, body: await r.json() };
        });
        eq("providers route status", providers.status, 200);
        eq("provider count", providers.body.providers.length, 3);
        eq(
            "provider ids",
            providers.body.providers.map((p) => p.id),
            ["cometapi", "grokified", "custom"],
        );
        eq("default provider", providers.body.defaultProvider, "cometapi");

        const models = await page.evaluate(async () => {
            const r = await fetch("/comfyui_llm_api/models?provider=cometapi");
            return { status: r.status, body: await r.json() };
        });
        eq("models route status", models.status, 200);
        check(
            "live cometapi model list is complete",
            models.body.models.length >= 10,
            `got ${models.body.models.length}`,
        );
        check("live list contains grok-4.7", models.body.models.includes("grok-4.7"));
        check("live list contains a cometapi-only id", models.body.models.includes("deepseek-v4.1-flash"));
        check(
            "live list excludes image-only ids",
            !models.body.models.some((m) => m.includes("flux")),
            JSON.stringify(models.body.models.filter((m) => m.includes("flux"))),
        );
        check("no error reported for cometapi", models.body.error === null, String(models.body.error));

        const grokifiedModels = await page.evaluate(async () => {
            const r = await fetch("/comfyui_llm_api/models?provider=grokified");
            return await r.json();
        });
        eq("grokified falls back to its static list", grokifiedModels.source, "fallback");
        check("fallback list still has models", grokifiedModels.models.length > 0);

        // --------------------------------------------------------------- //
        console.log("== workflow load: provider restored from the file ==");
        await page.evaluate((workflow) => window.app.loadGraphData(workflow), WORKFLOW);
        await page.waitForFunction(
            () => window.app.graph?._nodes?.some((n) => n.comfyClass === "LLMAPITextProcessor"),
            null,
            { timeout: 30_000 },
        );
        // let the extension's async sync settle
        await page.waitForTimeout(2500);

        const summary = await page.evaluate(() => {
            const node = window.app.graph._nodes.find((n) => n.comfyClass === "LLMAPITextProcessor");
            const w = (name) => node.widgets.find((x) => x.name === name);
            const el = node.widgets.find((x) => x.name === "provider_status")?.element;
            return {
                widgetNames: node.widgets.map((x) => x.name),
                provider: w("provider")?.value,
                model: w("model")?.value,
                modelValues: w("model")?.options?.values ?? null,
                effort: w("reasoning_effort")?.value,
                effortValues: w("reasoning_effort")?.options?.values ?? null,
                baseUrl: w("base_url")?.value,
                apiKeyPlaceholder: w("api_key")?.options?.placeholder,
                maxTokens: w("max_tokens")?.value,
                temperature: w("temperature")?.value,
                status: el?.textContent ?? null,
                statusIsDom: !!el,
            };
        });

        for (const name of ["provider", "model", "reasoning_effort", "base_url", "api_key", "model_override", "max_tokens_param"]) {
            check(`widget '${name}' exists`, summary.widgetNames.includes(name));
        }
        check(
            "refresh button widget exists",
            summary.widgetNames.some((n) => String(n).includes("Refresh model list")),
            JSON.stringify(summary.widgetNames),
        );
        check("status line is a DOM widget", summary.statusIsDom);

        eq("provider restored from the workflow", summary.provider, "Grokified API (grokified.com)");
        eq("saved model kept (valid for grokified)", summary.model, "grok-4.6");
        check("model combo narrowed to grokified", summary.modelValues.length <= 12, JSON.stringify(summary.modelValues));
        check("grokified list has no cometapi-only model", !summary.modelValues.includes("deepseek-v4.1-flash"));
        eq(
            "effort combo narrowed to grokified levels",
            summary.effortValues,
            ["default", "low", "medium", "high", "xhigh"],
        );
        eq("saved effort kept", summary.effort, "xhigh");
        eq("base_url follows the provider", summary.baseUrl, "https://api.grokified.com/v1");
        eq("api_key placeholder names the env var", summary.apiKeyPlaceholder, "empty = $GROKIFIED_API_KEY");
        check("status names the provider", (summary.status || "").includes("Grokified"), summary.status);
        check("status names the key env var", (summary.status || "").includes("GROKIFIED_API_KEY"), summary.status);
        eq("other widget values survive the sync", [summary.maxTokens, summary.temperature], [1024, 0.5]);

        // --------------------------------------------------------------- //
        console.log("== switching provider in the UI ==");
        await page.evaluate(async () => {
            const node = window.app.graph._nodes.find((n) => n.comfyClass === "LLMAPITextProcessor");
            const widget = node.widgets.find((x) => x.name === "provider");
            widget.value = "CometAPI (cometapi.com)";
            widget.callback?.(widget.value);
            await window.__LLM_API__.applyProvider(node, widget.value);
        });
        await page.waitForTimeout(2500);

        const after = await page.evaluate(() => {
            const node = window.app.graph._nodes.find((n) => n.comfyClass === "LLMAPITextProcessor");
            const w = (name) => node.widgets.find((x) => x.name === name);
            return {
                model: w("model")?.value,
                modelValues: w("model")?.options?.values ?? null,
                effortValues: w("reasoning_effort")?.options?.values ?? null,
                baseUrl: w("base_url")?.value,
                keyPlaceholder: w("api_key")?.options?.placeholder,
                status: node.widgets.find((x) => x.name === "provider_status")?.element?.textContent ?? null,
                property: node.properties?.llm_api_provider,
            };
        });

        check("model combo reloaded with the live cometapi list", after.modelValues.length >= 10, `got ${after.modelValues.length}`);
        check("cometapi model list includes deepseek-v4.1-flash", after.modelValues.includes("deepseek-v4.1-flash"));
        check("cometapi model list includes grok-4.7", after.modelValues.includes("grok-4.7"));
        eq(
            "effort combo widened to cometapi levels",
            after.effortValues,
            ["default", "minimal", "low", "medium", "high", "xhigh", "max"],
        );
        eq("base_url swapped automatically", after.baseUrl, "https://api.cometapi.com/v1");
        eq("api_key placeholder swapped", after.keyPlaceholder, "empty = $COMETAPI_API_KEY");
        check("model reset to the provider default (grok-4.6 is not on cometapi)", after.model === "grok-4.7", after.model);
        check("status names the new provider", (after.status || "").includes("CometAPI"), after.status);
        check("status shows no warning", !(after.status || "").includes("⚠"), after.status);
        eq("node property records the provider", after.property, "cometapi");

        // --------------------------------------------------------------- //
        console.log("== a custom base_url must survive a provider switch ==");
        const custom = await page.evaluate(async () => {
            const node = window.app.graph._nodes.find((n) => n.comfyClass === "LLMAPITextProcessor");
            const base = node.widgets.find((x) => x.name === "base_url");
            base.value = "https://my.own.server/v1";
            await window.__LLM_API__.applyProvider(node, "Grokified API (grokified.com)");
            const keptAfterGrokified = base.value;
            await window.__LLM_API__.applyProvider(node, "cometapi");
            return { keptAfterGrokified, afterCometapi: base.value };
        });
        eq("typed base_url is never overwritten", custom.keptAfterGrokified, "https://my.own.server/v1");
        eq("typed base_url survives switching back", custom.afterCometapi, "https://my.own.server/v1");

        // --------------------------------------------------------------- //
        console.log("== node is registered as a real node type ==");
        const objectInfo = await page.evaluate(async () => {
            const r = await fetch("/object_info/LLMAPITextProcessor");
            return await r.json();
        });
        const def = objectInfo.LLMAPITextProcessor;
        check("node type is registered", !!def);
        eq("node output names", def.output_name, ["RESPONSE", "REASONING", "PERF", "USAGE_JSON"]);
        eq("node category", def.category, "LLM/API");
        eq("provider widget choices come from the server", def.input.required.provider[0].length, 3);
        check("image input is optional", !!def.input.optional.image);
        check("VALIDATE_INPUTS makes the model list advisory", def.input.required.model[1].default === "grok-4.7");

        const listNode = await page.evaluate(async () => {
            const r = await fetch("/object_info/LLMAPIModelList");
            return await r.json();
        });
        check("model list node is registered", !!listNode.LLMAPIModelList);

        if (SHOT) {
            await page.evaluate(() => {
                const node = window.app.graph._nodes.find((n) => n.comfyClass === "LLMAPITextProcessor");
                window.app.canvas.selectNode?.(node);
                window.app.canvas.centerOnNode?.(node);
                window.app.canvas.setZoom?.(1.1);
                // nudge the graph down so the floating toolbar stops covering the title
                window.app.canvas.ds.offset[1] += 110;
                window.app.canvas.setDirty(true, true);
            });
            await page.waitForTimeout(1200);
            await page.screenshot({ path: path.resolve(SHOT) });
            console.log(`  screenshot -> ${SHOT}`);
        }

        // Unrelated packs and the browser itself 404 on optional assets; only
        // failures touching this pack matter.
        const mine = notFound.filter((url) => /llm_api|ComfyUI-LLM-API/i.test(url));
        check("no 404 from this pack's assets", mine.length === 0, mine.join(" | "));
        if (notFound.length && mine.length === 0) {
            console.log(`  (info) ${notFound.length} unrelated 404(s), e.g. ${notFound[0]}`);
        }

        // Other custom nodes (KJNodes, rgthree, ...) throw on this machine too,
        // so a failure is only attributed to this pack when it names it.
        const ours = consoleErrors.filter((text) => /llm_api|LLM\.API\.MultiProvider|LLMAPITextProcessor/i.test(text));
        const foreign = consoleErrors.filter((text) => !ours.includes(text) && !/404 \(Not Found\)/.test(text));
        check("no console error from this pack", ours.length === 0, ours.slice(0, 3).join(" | "));
        if (foreign.length) {
            console.log(`  (info) ${foreign.length} error(s) raised by other packs, e.g. ${foreign[0].slice(0, 120)}`);
        }
    } catch (error) {
        check("test run completed", false, `${error.name}: ${error.message}`);
    } finally {
        await browser.close();
    }

    console.log("");
    console.log(`${PASSED.length} lulus, ${FAILED.length} gagal`);
    for (const failure of FAILED) console.log(`  GAGAL: ${failure}`);
    process.exit(FAILED.length ? 1 : 0);
})();
