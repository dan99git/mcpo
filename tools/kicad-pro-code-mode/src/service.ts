import type { CallToolResult, Tool } from "@modelcontextprotocol/sdk/types.js";
import { ToolCatalog, type SearchResult } from "./catalog.js";
import type { UpstreamBridge } from "./upstream.js";

export interface SearchResponse {
  query: string;
  totalAvailable: number;
  returned: number;
  tools: SearchResult[];
}

export class CodeModeService {
  private readonly catalog = new ToolCatalog();

  constructor(private readonly upstream: UpstreamBridge) {
    upstream.onToolsChanged((tools) => this.catalog.replace(tools));
  }

  async initialize(): Promise<number> {
    this.catalog.replace(await this.upstream.connect());
    return this.catalog.size;
  }

  async search(query: string, limit: number): Promise<SearchResponse> {
    this.catalog.replace(await this.upstream.refreshTools());
    const tools = this.catalog.search(query, limit);
    return {
      query,
      totalAvailable: this.catalog.size,
      returned: tools.length,
      tools,
    };
  }

  async describe(name: string): Promise<Tool> {
    let tool = this.catalog.get(name);
    if (!tool) {
      this.catalog.replace(await this.upstream.refreshTools());
      tool = this.catalog.get(name);
    }
    if (!tool) {
      const suggestions = this.catalog.search(name, 3).map((item) => item.name);
      const suffix = suggestions.length
        ? ` Similar tools: ${suggestions.join(", ")}`
        : "";
      throw new Error(`Unknown KiCad tool: ${name}.${suffix}`);
    }
    return tool;
  }

  async execute(
    name: string,
    args: Record<string, unknown>,
  ): Promise<CallToolResult> {
    if (!this.catalog.has(name)) {
      await this.describe(name);
    }
    return this.upstream.callTool(name, args);
  }

  async close(): Promise<void> {
    await this.upstream.close();
  }
}

