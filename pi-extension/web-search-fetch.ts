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
	engines: Type.Optional(Type.String({ description: "Optional SearXNG engines, comma-separated, e.g. 'google cse', 'github', or 'arxiv'. Pass 'auto' to route by query keywords (e.g. Reddit/opinions, academic, errors, code, documents, social)." })),
	time_range: Type.Optional(Type.String({ description: "Optional time range: day, month, or year." })),
	mode: Type.Optional(StringEnum(["fast", "balanced", "deep"] as const, { description: "Search strategy: fast uses the first usable free engine; balanced adds a second engine only for weak results; deep combines up to three free engines." })),
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
			"Use web_search when the user asks for current web information, internet research, recent docs, product pages, news, or URLs.",
			"For focused technical searches, web_search can select google cse, github, arxiv, or wikipedia with the engines parameter instead of another general engine.",
			"Pass engines='auto' to let the API route by query intent (Reddit/opinions, academic papers, errors/stack traces, code, documents, social).",
			"After web_search finds a likely source, use web_fetch to retrieve the page text when details or citations are needed.",
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
					engines: params.engines,
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
					content: [{ type: "text", text: `web_search is currently unavailable: ${message}\n\nThe search backend may be down or all engines are rate-limited. Suggested contingencies: retry once, use web_fetch on a known URL, or proceed using your existing knowledge and tell the user live web search is unavailable.` }],
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
