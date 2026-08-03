import type { Tool } from "@modelcontextprotocol/sdk/types.js";

export interface SearchResult {
  name: string;
  title?: string;
  description?: string;
  argumentNames: string[];
  requiredArguments: string[];
  annotations?: Tool["annotations"];
}

interface ScoredTool {
  tool: Tool;
  matchedTerms: number;
  score: number;
}

function normalizedTerms(value: string): string[] {
  return value
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .toLowerCase()
    .split(/[^a-z0-9]+/)
    .filter(Boolean);
}

function scoreTool(tool: Tool, query: string, terms: string[]): ScoredTool | null {
  const name = tool.name.toLowerCase();
  const title = (tool.title ?? "").toLowerCase();
  const description = (tool.description ?? "").toLowerCase();
  const nameTerms = new Set(normalizedTerms(tool.name));
  let score = 0;
  let matchedTerms = 0;

  if (name === query) {
    score += 1_000;
  } else if (name.startsWith(query)) {
    score += 400;
  } else if (name.includes(query)) {
    score += 250;
  }

  for (const term of terms) {
    let matched = false;
    if (nameTerms.has(term)) {
      score += 100;
      matched = true;
    } else if (name.includes(term)) {
      score += 60;
      matched = true;
    }
    if (title.includes(term)) {
      score += 25;
      matched = true;
    }
    if (description.includes(term)) {
      score += 10;
      matched = true;
    }
    if (matched) {
      matchedTerms += 1;
    }
  }

  return matchedTerms === 0 ? null : { tool, matchedTerms, score };
}

function compactResult(tool: Tool): SearchResult {
  const properties = tool.inputSchema.properties ?? {};
  return {
    name: tool.name,
    ...(tool.title ? { title: tool.title } : {}),
    ...(tool.description ? { description: tool.description } : {}),
    argumentNames: Object.keys(properties),
    requiredArguments: tool.inputSchema.required ?? [],
    ...(tool.annotations ? { annotations: structuredClone(tool.annotations) } : {}),
  };
}

export class ToolCatalog {
  private tools = new Map<string, Tool>();

  replace(tools: Tool[]): void {
    const next = new Map<string, Tool>();
    for (const tool of tools) {
      if (next.has(tool.name)) {
        throw new Error(`Upstream returned duplicate tool name: ${tool.name}`);
      }
      next.set(tool.name, structuredClone(tool));
    }
    if (next.size === 0) {
      throw new Error("Upstream returned no tools");
    }
    this.tools = next;
  }

  get size(): number {
    return this.tools.size;
  }

  has(name: string): boolean {
    return this.tools.has(name);
  }

  get(name: string): Tool | undefined {
    const tool = this.tools.get(name);
    return tool ? structuredClone(tool) : undefined;
  }

  search(rawQuery: string, limit: number): SearchResult[] {
    const query = rawQuery.trim().toLowerCase();
    if (!query || query === "*") {
      return [...this.tools.values()]
        .sort((left, right) => left.name.localeCompare(right.name))
        .slice(0, limit)
        .map(compactResult);
    }

    const terms = normalizedTerms(query);
    return [...this.tools.values()]
      .map((tool) => scoreTool(tool, query, terms))
      .filter((entry): entry is ScoredTool => entry !== null)
      .sort(
        (left, right) =>
          right.matchedTerms - left.matchedTerms ||
          right.score - left.score ||
          left.tool.name.localeCompare(right.tool.name),
      )
      .slice(0, limit)
      .map(({ tool }) => compactResult(tool));
  }
}
