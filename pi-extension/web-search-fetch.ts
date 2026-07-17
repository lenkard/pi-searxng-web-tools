import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { StringEnum } from "@earendil-works/pi-ai";
import { Type } from "typebox";

const DEFAULT_BASE_URL = "http://172.25.0.7:8889"; // Kinkaid via WireGuard

function getBaseUrl(): string {
	return (process.env.PI_WEB_API_BASE_URL || DEFAULT_BASE_URL).replace(/\/+$/, "");
}

function asErrorText(error: unknown): string {
	return error instanceof Error ? error.message : String(error);
}

function getHeaders(): Record<string, string> {
	const headers: Record<string, string> = { "content-type": "application/json" };
	const apiKey = process.env.PI_WEB_API_KEY;
	if (apiKey) headers["x-api-key"] = apiKey;
	return headers;
}

async function fetchJson(path: string, body: unknown, signal?: AbortSignal): Promise<any> {
	const baseUrl = getBaseUrl();
	const response = await fetch(`${baseUrl}${path}`, {
		method: "POST",
		headers: getHeaders(),
		body: JSON.stringify(body),
		signal,
	});

	const text = await response.text();
	let data: any;
	try {
		data = text ? JSON.parse(text) : {};
	} catch {
		data = { raw: text };
	}

	if (!response.ok) {
		throw new Error(`HTTP ${response.status}: ${typeof data?.detail === "string" ? data.detail : text}`);
	}
	return data;
}

const WebSearchParams = Type.Object({
	q: Type.String({ description: "Search query." }),
	max_results: Type.Optional(Type.Integer({ minimum: 1, maximum: 50, default: 10, description: "Maximum number of results to return." })),
	pageno: Type.Optional(Type.Integer({ minimum: 1, default: 1, description: "Search result page number." })),
	language: Type.Optional(Type.String({ default: "auto", description: "Language code, 'auto', or 'all'." })),
	categories: Type.Optional(Type.String({ description: "Optional SearXNG categories, comma-separated, e.g. 'general,news'." })),
	engines: Type.Optional(Type.String({ default: "auto", description: "Use 'auto' unless the user explicitly requests a specific source. An explicit request may name general engines and at most one Google CSE." })),
	time_range: Type.Optional(Type.String({ description: "Optional time range: day, month, or year." })),
	mode: Type.Optional(StringEnum(["fast", "balanced", "deep"] as const, { description: "Search strategy: fast uses the first usable provider; balanced adds a second provider only for weak results; deep combines up to three providers." })),
});

const WebFetchParams = Type.Object({
	url: Type.String({ description: "URL to fetch and extract readable text from." }),
	max_chars: Type.Optional(Type.Integer({ minimum: 100, maximum: 50000, default: 20000, description: "Maximum extracted text characters to return (capped to protect agent context)." })),
});

export default function webSearchFetchExtension(pi: ExtensionAPI) {
	pi.registerTool({
		name: "web_search",
		label: "Web Search",
		description: "Search the web using the local SearXNG-backed API and return JSON search results.",
		promptSnippet: "Search the web using the local SearXNG-backed API",
		promptGuidelines: [
			"Canonical workflow: use engines='auto' and mode='balanced', then use web_fetch on promising results before relying on or citing them.",
			"Choose an explicit engine only when the user explicitly requests that source; never send more than one Google CSE in a request.",
			"Do not probe, sweep, benchmark, or cycle through engines. Do not repeat a rate-limited or unavailable-engine request; the API handles health, cooldowns, confirmation, recovery, and fallback.",
			"If results are weak, refine the query once before changing mode or source. Use deep mode only when the user requests broad research.",
			"Use web_search for current information, internet research, recent docs, product pages, news, or URLs. Use web_fetch for page details and citations.",
		],
		parameters: WebSearchParams,
		async execute(_toolCallId, params, signal, onUpdate) {
			onUpdate?.({ content: [{ type: "text", text: `Searching web for: ${params.q}` }] });
			try {
				const data = await fetchJson("/websearch", {
					q: params.q,
					max_results: params.max_results ?? 10,
					pageno: params.pageno ?? 1,
					language: params.language ?? "auto",
					categories: params.categories,
					engines: params.engines ?? "auto",
					time_range: params.time_range,
					mode: params.mode ?? "balanced",
				}, signal);

				const results = Array.isArray(data.results) ? data.results : [];
				const lines = results.map((r: any, i: number) => {
					const title = r.title || "Untitled";
					const url = r.url || "";
					const content = r.content ? `\n   ${r.content}` : "";
					return `${i + 1}. ${title}\n   ${url}${content}`;
				});

				const failures = Array.isArray(data.unresponsive_engines) ? data.unresponsive_engines : [];
				const failureText = failures.length
					? `\n\nUpstream engine failures: ${failures.map((item: any) => Array.isArray(item) ? item.join(": ") : String(item)).join("; ")}`
					: "";

				const excluded = Array.isArray(data.excluded_unavailable) ? data.excluded_unavailable : [];
				const excludedText = excluded.length
					? `\n\nNote: unavailable search engine(s) were skipped: ${excluded.join(", ")}.`
					: "";

				return {
					content: [{ type: "text", text: lines.length ? `${lines.join("\n\n")}${failureText}${excludedText}` : `No search results found.${failureText}${excludedText}` }],
					details: data,
				};
			} catch (error) {
				// Contingency: surface the failure to the agent instead of throwing, so
				// the agent can adapt (retry, use web_fetch, or proceed without live web).
				const message = asErrorText(error);
				return {
					content: [{ type: "text", text: `web_search is currently unavailable: ${message}\n\nDo not loop through engines. Retry once only for a transient connection error; never retry a reported rate limit. Otherwise use web_fetch on a known URL or proceed without live search and tell the user.` }],
					details: { error: message, degraded: true },
				};
			}
		},
	});

	pi.registerTool({
		name: "web_fetch",
		label: "Web Fetch",
		description: "Fetch a URL and extract readable page text using the local webfetch API.",
		promptSnippet: "Fetch and extract readable text from a URL",
		promptGuidelines: [
			"Use web_fetch to read pages returned by web_search or URLs provided by the user.",
			"Do not use web_fetch for local files; use the read tool for local filesystem paths.",
		],
		parameters: WebFetchParams,
		async execute(_toolCallId, params, signal, onUpdate) {
			onUpdate?.({ content: [{ type: "text", text: `Fetching: ${params.url}` }] });
			try {
				const data = await fetchJson("/webfetch", {
					url: params.url,
					max_chars: params.max_chars ?? 20000,
				}, signal);

				const title = data.title ? `# ${data.title}\n\n` : "";
				const url = data.url ? `URL: ${data.url}\n\n` : "";
				const truncated = data.truncated ? "\n\n[Text truncated]" : "";
				return {
					content: [{ type: "text", text: `${title}${url}${data.text || ""}${truncated}` }],
					details: data,
				};
			} catch (error) {
				throw new Error(`web_fetch failed: ${asErrorText(error)}`);
			}
		},
	});

	pi.registerCommand("webapi-status", {
		description: "Check the local web search/fetch API health endpoint.",
		handler: async (_args, ctx) => {
			try {
				const response = await fetch(`${getBaseUrl()}/health`);
				const text = await response.text();
				ctx.ui.notify(`Web API ${response.status}: ${text}`, response.ok ? "info" : "warning");
			} catch (error) {
				ctx.ui.notify(`Web API check failed: ${asErrorText(error)}`, "error");
			}
		},
	});

	pi.on("session_start", (_event, ctx) => {
		ctx.ui.setStatus("web-api", `web-api: ${getBaseUrl()}`);
	});
}
