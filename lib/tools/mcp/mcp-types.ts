export interface MCPServerConfig {
  command: string;
  args: string[];
  env?: Record<string, string>;
  enabled: boolean;
}

export type JsonPrimitive = string | number | boolean | null;
export type JsonValue = JsonPrimitive | JsonValue[] | JsonObject;
export interface JsonObject {
  [key: string]: JsonValue;
}

export interface JsonSchema {
  type?: string;
  properties?: Record<string, JsonSchema>;
  items?: JsonSchema | JsonSchema[];
  required?: string[];
  enum?: JsonValue[];
  description?: string;
  [key: string]: JsonValue | JsonSchema | JsonSchema[] | string | string[] | undefined;
}

export interface MCPTool {
  name: string;
  description: string;
  parameters: JsonSchema;
}

export interface MCPToolDefinition {
  name: string;
  description?: string;
  inputSchema?: JsonSchema;
}

export interface MCPToolContent {
  [key: string]: JsonValue | undefined;
  type?: string;
  text?: string;
}

export interface MCPToolCallArgs {
  name: string;
  arguments: Record<string, JsonValue>;
}

export interface MCPToolCallResult {
  content?: MCPToolContent[];
  isError?: boolean;
}

export interface MCPToolClient {
  connect(transport: MCPTransport): Promise<void>;
  listTools(): Promise<{ tools: MCPToolDefinition[] }>;
  callTool(args: MCPToolCallArgs): Promise<MCPToolCallResult>;
  initialize?(): Promise<void> | void;
  close?(): Promise<void> | void;
}

export interface MCPTransport {
  close?(): Promise<void> | void;
}

export type MCPClientConstructor = new (
  clientInfo: { name: string; version: string },
  options: { capabilities: Record<string, unknown> }
) => MCPToolClient;

export type MCPTransportConstructor = new (options: {
  command: string;
  args?: string[];
  env?: Record<string, string>;
}) => MCPTransport;

export interface MCPToolEntry {
  key: string;
  serverName: string;
  client: MCPToolClient;
  definition: MCPToolDefinition;
}

export interface MCPServer {
  name: string;
  toolCount: number;
  tools: Array<{
    key: string;
    name: string;
    description: string;
  }>;
}
