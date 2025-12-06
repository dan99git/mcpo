/**
 * MCP Client
 * 
 * ROLE:
 * The central coordinator for Model Context Protocol (MCP) integrations.
 * It manages connections to external MCP servers (filesystem, devtools, etc.)
 * and proxies tool execution requests to them.
 * 
 * ARCHITECTURE:
 * - Uses `@modelcontextprotocol/sdk` (loaded dynamically via `getSdk`) to establish transports (Stdio).
 * - Manages a collection of `MCPToolClient` instances, one per server.
 * - Maintains a merged registry of all tools available across all connected servers.
 * - Watches `mcp-config.json` for hot-reloading server configurations.
 * 
 * SAFETY & SECURITY:
 * - Executes tools in external processes via Stdio or HTTP.
 * - `callTool` delegates execution to the external server. The safety of the tool depends on the server's implementation.
 * - "Hot Reload" logic restarts connections; ensure this doesn't interrupt active tool calls.
 * 
 * KEY METHODS:
 * - `initialize()`: Bootstraps connections based on config.
 * - `callTool()`: The primary entry point for `McpToolExecutor`.
 * - `connectServer()` / `disconnectServer()`: Dynamic lifecycle management.
 */
import { EventEmitter } from 'events';
import path from 'path';
import fs from 'fs';
import { LoggingService } from '../../../core/logging';
import { SettingsService } from '../../../core/settings-service';
import {
  MCPServerConfig,
  MCPTool,
  MCPToolEntry,
  MCPServer,
  MCPToolDefinition,
  MCPToolClient,
  MCPTransport,
  MCPClientConstructor,
  MCPTransportConstructor,
  MCPToolCallArgs,
  MCPToolCallResult,
  MCPToolContent,
  JsonSchema,
  JsonValue
} from './mcp-types';

const isPlainObject = (value: unknown): value is Record<string, unknown> => {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
};

const isJsonSchema = (value: unknown): value is JsonSchema => {
  if (!isPlainObject(value)) {
    return false;
  }

  const candidate = value as Partial<JsonSchema>;
  if ('type' in candidate && candidate.type !== undefined && typeof candidate.type !== 'string') {
    return false;
  }

  return true;
};

const isToolContent = (value: unknown): value is MCPToolContent => isPlainObject(value);

const normalizeToolParameters = (schema?: JsonSchema): JsonSchema => {
  if (schema && isJsonSchema(schema)) {
    return schema;
  }

  return {
    type: 'object',
    properties: {},
    required: []
  };
};

const normalizeToolDefinition = (tool: MCPToolDefinition | unknown, fallbackName: string): MCPToolDefinition => {
  if (!isPlainObject(tool)) {
    return {
      name: fallbackName
    };
  }

  const descriptor = tool as Record<string, unknown>;
  const name = typeof descriptor.name === 'string' && descriptor.name.trim().length > 0
    ? descriptor.name
    : fallbackName;
  const description = typeof descriptor.description === 'string' ? descriptor.description : undefined;

  const rawSchema = (descriptor.inputSchema ?? descriptor['input_schema']) as unknown;
  const inputSchema = isJsonSchema(rawSchema) ? rawSchema : undefined;

  return {
    name,
    description,
    inputSchema
  };
};

const normalizeToolCallResult = (result: unknown): MCPToolCallResult => {
  if (!isPlainObject(result)) {
    return {};
  }

  const candidate = result as Record<string, unknown>;
  const contentRaw = candidate.content;
  const content = Array.isArray(contentRaw) ? contentRaw.filter(isToolContent) : [];

  return {
    content,
    isError: Boolean(candidate.isError)
  };
};

export class McpClient extends EventEmitter {
  private configPath: string;
  private clients: Map<string, MCPToolClient> = new Map();
  private tools: Map<string, MCPToolEntry> = new Map();
  private transports: Map<string, MCPTransport> = new Map();
  private logger: LoggingService;
  private settings: SettingsService | null;
  private initializing: Promise<void> | null = null;
  private watcher: fs.FSWatcher | null = null;
  private reloadDebounceTimer: NodeJS.Timeout | null = null;

  constructor(configPath: string, logger: LoggingService, settings?: SettingsService) {
    super();
    this.configPath = configPath;
    this.logger = logger;
    this.settings = settings ?? null;
  }

  async initialize(): Promise<void> {
    if (this.initializing) {
      await this.initializing;
      return;
    }

    if (!this.configPath) {
      this.logger.warn('[MCP] No configuration path provided. Skipping MCP initialization.', { category: 'mcp' });
      return;
    }

    this.initializing = this._doInitialize();

    try {
      await this.initializing;
      this._setupHotReload();
    } finally {
      this.initializing = null;
    }
  }

  private async _doInitialize(): Promise<void> {
    try {
      const config = await this.loadConfig();
      await this.shutdown();

      const servers = config?.mcpServers ?? {};
      const enabledServers = Object.entries(servers).filter(([, server]) => server && server.enabled !== false);

      // Read persisted disabled servers from settings
      let disabledServers: string[] = [];
      if (this.settings) {
        try {
          disabledServers = await this.settings.get('mcp_disabled_servers', []) ?? [];
          if (disabledServers.length > 0) {
            this.logger.info(`[MCP] Found ${disabledServers.length} persisted disabled servers: ${disabledServers.join(', ')}`, { category: 'mcp' });
          }
        } catch (err) {
          this.logger.warn('[MCP] Failed to load disabled servers from settings, continuing with all enabled', { category: 'mcp' });
        }
      }

      // Filter out persisted disabled servers
      const serversToConnect = enabledServers.filter(([name]) => !disabledServers.includes(name));

      this.logger.info(`[MCP] Found ${enabledServers.length} enabled servers in config, ${serversToConnect.length} will be connected`, { category: 'mcp' });

      for (const [name, serverConfig] of serversToConnect) {
        try {
          await this._connectServer(name, serverConfig);
        } catch (error) {
          const errorMessage = error instanceof Error ? error.message : String(error);
          const errorInstance = error instanceof Error ? error : new Error(errorMessage);
          this.logger.error(`[MCP] Failed to connect to ${name}: ${errorMessage}`, errorInstance, { category: 'mcp' });
        }
      }

      this.logger.info(
        `[MCP] Ready. Connected servers: ${this.clients.size}. Registered tools: ${this.tools.size}`,
        { category: 'mcp' }
      );
    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : String(error);
      const errorInstance = error instanceof Error ? error : new Error(errorMessage);
      this.logger.error('[MCP] Failed to initialize:', errorInstance, { category: 'mcp' });
      throw error;
    }
  }

  private async loadConfig(): Promise<{ mcpServers: Record<string, MCPServerConfig> }> {
    const resolvedPath = path.isAbsolute(this.configPath)
      ? this.configPath
      : path.resolve(process.cwd(), this.configPath);

    this.logger.info(`[MCP] Loading config from ${resolvedPath}`, { category: 'mcp' });

    if (!fs.existsSync(resolvedPath)) {
      const error = new Error(`MCP config not found: ${resolvedPath}`);
      this.logger.error(`[MCP] ${error.message}`, error, { category: 'mcp' });
      throw error;
    }

    try {
      const configContent = fs.readFileSync(resolvedPath, 'utf8');
      const config = JSON.parse(configContent);

      if (!config.mcpServers || typeof config.mcpServers !== 'object') {
        throw new Error('Invalid MCP config: missing or invalid mcpServers field');
      }

      return config;
    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : String(error);
      const errorInstance = error instanceof Error ? error : new Error(errorMessage);
      this.logger.error(`[MCP] Failed to parse config file: ${errorMessage}`, errorInstance, { category: 'mcp' });
      throw error;
    }
  }

  private _setupHotReload(): void {
    if (this.watcher) {
      return; // Already watching
    }

    const resolvedPath = path.isAbsolute(this.configPath)
      ? this.configPath
      : path.resolve(process.cwd(), this.configPath);

    if (!fs.existsSync(resolvedPath)) {
      return;
    }

    this.logger.info('[MCP] Hot reload enabled. Watching for config changes...', { category: 'mcp' });

    this.watcher = fs.watch(resolvedPath, (eventType) => {
      if (eventType !== 'change') {
        return;
      }

      // Debounce multiple rapid changes
      if (this.reloadDebounceTimer) {
        clearTimeout(this.reloadDebounceTimer);
      }

      this.reloadDebounceTimer = setTimeout(() => {
        this.logger.info('[MCP] Config file changed. Reloading...', { category: 'mcp' });
        this._reloadConfig().catch((err) => {
          const errorMessage = err instanceof Error ? err.message : String(err);
          const errorInstance = err instanceof Error ? err : new Error(errorMessage);
          this.logger.error(`[MCP] Hot reload failed: ${errorMessage}`, errorInstance, { category: 'mcp' });
        });
      }, 500);
    });
  }

  private async _reloadConfig(): Promise<void> {
    await this.initialize();
    this.logger.info('[MCP] Hot reload complete.', { category: 'mcp' });
  }

  private async getSdk(): Promise<{ ClientCtor: MCPClientConstructor; TransportCtor: MCPTransportConstructor } | null> {
    try {
      const clientModule = (await import('@modelcontextprotocol/sdk/client/index.js')) as Partial<{ Client: MCPClientConstructor }>;
      const transportModule = (await import('@modelcontextprotocol/sdk/client/stdio.js')) as Partial<{ StdioClientTransport: MCPTransportConstructor }>;

      if (!clientModule.Client || !transportModule.StdioClientTransport) {
        this.logger.warn('[MCP] SDK exports missing expected constructors; MCP features disabled', { category: 'mcp' });
        return null;
      }

      return {
        ClientCtor: clientModule.Client,
        TransportCtor: transportModule.StdioClientTransport
      };
    } catch (error) {
      this.logger.warn('[MCP] SDK not available; MCP features disabled', { category: 'mcp' });
      return null;
    }
  }

  private async _connectServer(name: string, serverConfig: MCPServerConfig): Promise<void> {
    if (this.clients.has(name)) {
      this.logger.warn(`[MCP] Server ${name} is already connected`, { category: 'mcp' });
      return;
    }

    this.logger.info(`[MCP] Connecting to server: ${name}`, {
      category: 'mcp',
      command: serverConfig.command,
      args: serverConfig.args
    });

    const sdk = await this.getSdk();
    if (!sdk) {
      this.logger.warn(`[MCP] Skipping connection for ${name} because SDK is unavailable`, { category: 'mcp' });
      return;
    }

    const transport = new sdk.TransportCtor({
      command: serverConfig.command,
      args: serverConfig.args ?? [],
      env: serverConfig.env ?? {}
    });

    // Handle EPIPE errors gracefully (broken pipe when process closes)
    if (transport && typeof transport === 'object' && 'stderr' in transport) {
      const stderr = (transport as { stderr?: NodeJS.ReadableStream }).stderr;
      if (stderr && typeof stderr.on === 'function') {
        stderr.on('error', (err: Error) => {
          if ((err as NodeJS.ErrnoException).code === 'EPIPE') {
            // Suppress EPIPE errors - this is expected when the connection closes
            return;
          }
          this.logger.warn(`[MCP] Stderr error from ${name}: ${err.message}`, { category: 'mcp' });
        });
      }
    }

    // Suppress uncaught EPIPE errors on the transport itself
    const transportAsEventEmitter = transport as unknown as { on?: (event: string, handler: (err: Error) => void) => void };
    if (transportAsEventEmitter && typeof transportAsEventEmitter.on === 'function') {
      transportAsEventEmitter.on('error', (err: Error) => {
        if ((err as NodeJS.ErrnoException).code === 'EPIPE') {
          // Suppress EPIPE errors - connection is closing
          this.logger.debug(`[MCP] Transport for ${name} closed (EPIPE)`, { category: 'mcp' });
          return;
        }
        this.logger.error(`[MCP] Transport error for ${name}: ${err.message}`, err, { category: 'mcp' });
      });
    }

    const client = new sdk.ClientCtor(
      {
        name: 'dali-gateway',
        version: '2.0.0'
      },
      {
        capabilities: {}
      }
    );

    // Handle client errors gracefully
    const clientAsEventEmitter = client as unknown as { on?: (event: string, handler: (err: Error) => void) => void };
    if (clientAsEventEmitter && typeof clientAsEventEmitter.on === 'function') {
      clientAsEventEmitter.on('error', (err: Error) => {
        if ((err as NodeJS.ErrnoException).code === 'EPIPE') {
          this.logger.debug(`[MCP] Client for ${name} closed (EPIPE)`, { category: 'mcp' });
          return;
        }
        this.logger.error(`[MCP] Client error for ${name}: ${err.message}`, err, { category: 'mcp' });
      });
    }

    try {
      await client.connect(transport);

      if (client.initialize) {
        await client.initialize();
      }
    } catch (error) {
      // Clean up on connection failure
      try {
        if (transport.close) {
          await transport.close();
        }
      } catch (closeError) {
        // Ignore cleanup errors
      }
      throw error;
    }

    this.clients.set(name, client);
    this.transports.set(name, transport);

    const { tools } = await client.listTools();
    const normalizedTools = tools.map((tool, index) => {
      const fallbackName = typeof tool.name === 'string' && tool.name.trim().length > 0
        ? tool.name
        : `tool_${index + 1}`;
      return normalizeToolDefinition(tool, fallbackName);
    });

    for (const definition of normalizedTools) {
      const key = `${name}_${definition.name}`;
      this.tools.set(key, {
        key,
        serverName: name,
        client,
        definition
      });
    }

    this.logger.info(`[MCP] Connected to ${name}, registered ${normalizedTools.length} tools`, { category: 'mcp' });
    this.emit('serverConnected', name);
  }

  /**
   * Public method to connect a server dynamically
   * @param name Server name
   * @param serverConfig Server configuration
   */
  async connectServer(name: string, serverConfig: MCPServerConfig): Promise<void> {
    return this._connectServer(name, serverConfig);
  }

  /**
   * Disconnect a specific server
   * @param serverName Server name to disconnect
   */
  async disconnectServer(serverName: string): Promise<void> {
    if (!this.clients.has(serverName)) {
      this.logger.warn(`[MCP] Server ${serverName} is not connected`, { category: 'mcp' });
      return;
    }

    this.logger.info(`[MCP] Disconnecting server: ${serverName}`, { category: 'mcp' });

    const tasks: Promise<void>[] = [];

    // Remove all tools from this server
    const toolsToRemove: string[] = [];
    for (const [toolKey, toolEntry] of this.tools.entries()) {
      if (toolEntry.serverName === serverName) {
        toolsToRemove.push(toolKey);
      }
    }
    toolsToRemove.forEach(key => this.tools.delete(key));

    // Close client
    const client = this.clients.get(serverName);
    if (client) {
      this.scheduleClose(tasks, client, `client ${serverName}`);
    }

    const transport = this.transports.get(serverName);
    if (transport) {
      this.scheduleClose(tasks, transport, `transport ${serverName}`);
    }

    if (tasks.length > 0) {
      await Promise.allSettled(tasks);
    }

    // Remove from maps
    this.clients.delete(serverName);
    this.transports.delete(serverName);

    this.logger.info(`[MCP] Disconnected from ${serverName}, removed ${toolsToRemove.length} tools`, { category: 'mcp' });
    this.emit('serverDisconnected', serverName);
  }

  /**
   * Load a single server config from mcp-config.json
   * @param serverName Server name to load
   * @returns Server configuration or null if not found
   */
  async loadServerConfig(serverName: string): Promise<MCPServerConfig | null> {
    try {
      const config = await this.loadConfig();
      const servers = config?.mcpServers || {};
      const serverConfig = servers[serverName];

      if (!serverConfig) {
        this.logger.warn(`[MCP] Server config not found: ${serverName}`, { category: 'mcp' });
        return null;
      }

      return serverConfig;
    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : String(error);
      const errorInstance = error instanceof Error ? error : new Error(errorMessage);
      this.logger.error(`[MCP] Failed to load server config for ${serverName}: ${errorMessage}`, errorInstance, { category: 'mcp' });
      return null;
    }
  }

  async shutdown(): Promise<void> {
    const tasks: Promise<void>[] = [];

    for (const [name, client] of this.clients.entries()) {
      if (client) {
        this.scheduleClose(tasks, client, `client ${name}`);
      }
    }

    for (const [name, transport] of this.transports.entries()) {
      if (transport) {
        this.scheduleClose(tasks, transport, `transport ${name}`);
      }
    }

    if (tasks.length > 0) {
      await Promise.allSettled(tasks);
    }

    this.clients.clear();
    this.transports.clear();
    this.tools.clear();

    // Clean up file watcher
    if (this.watcher) {
      this.watcher.close();
      this.watcher = null;
    }

    if (this.reloadDebounceTimer) {
      clearTimeout(this.reloadDebounceTimer);
      this.reloadDebounceTimer = null;
    }
  }

  private scheduleClose(tasks: Promise<void>[], closable: MCPToolClient | MCPTransport, label: string): void {
    const closeFn = closable.close;
    if (!closeFn) {
      return;
    }

    tasks.push(
      Promise.resolve(closeFn.call(closable)).catch((error) => {
        const errorMessage = error instanceof Error ? error.message : String(error);
        const errorInstance = error instanceof Error ? error : new Error(errorMessage);
        this.logger.error(`[MCP] Failed to close ${label}: ${errorMessage}`, errorInstance, { category: 'mcp' });
      })
    );
  }

  getAvailableTools(): MCPTool[] {
    return Array.from(this.tools.values()).map(({ key, definition }) => ({
      name: key,
      description: definition.description || '',
      parameters: normalizeToolParameters(definition.inputSchema)
    }));
  }

  getAvailableServers(): MCPServer[] {
    const serverMap = new Map<string, MCPServer>();

    for (const [toolKey, toolEntry] of this.tools.entries()) {
      const serverName = toolEntry.serverName;
      if (!serverMap.has(serverName)) {
        serverMap.set(serverName, {
          name: serverName,
          toolCount: 0,
          tools: []
        });
      }

      const server = serverMap.get(serverName)!;
      server.toolCount++;
      server.tools.push({
        key: toolKey,
        name: toolEntry.definition.name,
        description: toolEntry.definition.description || ''
      });
    }

    return Array.from(serverMap.values());
  }

  getToolsByServer(serverName: string): MCPTool[] {
    return Array.from(this.tools.values())
      .filter(({ serverName: sn }) => sn === serverName)
      .map(({ key, definition }) => ({
        name: key,
        description: definition.description || '',
        parameters: normalizeToolParameters(definition.inputSchema)
      }));
  }

  async callTool(name: string, args: Record<string, JsonValue> = {}): Promise<{ content: MCPToolContent[]; isError: boolean }> {
    const entry = this.tools.get(name);
    if (!entry) {
      throw new Error(`MCP tool not found: ${name}`);
    }

    const { client, definition } = entry;
    const callArgs: MCPToolCallArgs = {
      name: definition.name,
      arguments: args
    };
    const result = await client.callTool(callArgs);
    const normalizedResult = normalizeToolCallResult(result);

    return {
      content: normalizedResult.content ?? [],
      isError: Boolean(normalizedResult.isError)
    };
  }

  isServerEnabled(serverName: string): boolean {
    return this.clients.has(serverName);
  }

  async toggleServer(serverName: string, enabled: boolean): Promise<void> {
    if (enabled) {
      // Check if already connected
      if (this.clients.has(serverName)) {
        this.logger.info(`[MCP] Server ${serverName} is already connected`, { category: 'mcp' });
        this.emit('serverToggled', serverName, true);
        return;
      }

      // Load config and connect
      const serverConfig = await this.loadServerConfig(serverName);
      if (!serverConfig) {
        const error = new Error(`Server config not found: ${serverName}`);
        this.logger.error(`[MCP] Failed to toggle server ${serverName}: ${error.message}`, error, { category: 'mcp' });
        throw error;
      }

      try {
        await this.connectServer(serverName, serverConfig);
        
        // Remove from persisted disabled list
        await this._removeFromDisabledServers(serverName);
        
        this.emit('serverToggled', serverName, true);
        this.logger.info(`[MCP] Server ${serverName} enabled successfully`, { category: 'mcp' });
      } catch (error) {
        const errorMessage = error instanceof Error ? error.message : String(error);
        const errorInstance = error instanceof Error ? error : new Error(errorMessage);
        this.logger.error(`[MCP] Failed to enable server ${serverName}: ${errorMessage}`, errorInstance, { category: 'mcp' });
        throw error;
      }
    } else {
      // Disconnect if connected
      if (!this.clients.has(serverName)) {
        this.logger.info(`[MCP] Server ${serverName} is already disconnected`, { category: 'mcp' });
        
        // Still persist the disabled state even if not currently connected
        await this._addToDisabledServers(serverName);
        
        this.emit('serverToggled', serverName, false);
        return;
      }

      try {
        await this.disconnectServer(serverName);
        
        // Add to persisted disabled list
        await this._addToDisabledServers(serverName);
        
        this.emit('serverToggled', serverName, false);
        this.logger.info(`[MCP] Server ${serverName} disabled successfully`, { category: 'mcp' });
      } catch (error) {
        const errorMessage = error instanceof Error ? error.message : String(error);
        const errorInstance = error instanceof Error ? error : new Error(errorMessage);
        this.logger.error(`[MCP] Failed to disable server ${serverName}: ${errorMessage}`, errorInstance, { category: 'mcp' });
        throw error;
      }
    }
  }

  /**
   * Helper: Add server to persisted disabled list
   */
  private async _addToDisabledServers(serverName: string): Promise<void> {
    if (!this.settings) return;
    
    try {
      const current = await this.settings.get('mcp_disabled_servers', []) ?? [];
      if (!current.includes(serverName)) {
        current.push(serverName);
        await this.settings.set('mcp_disabled_servers', current);
        this.logger.info(`[MCP] Persisted disabled state for server: ${serverName}`, { category: 'mcp' });
      }
    } catch (err) {
      this.logger.warn(`[MCP] Failed to persist disabled state for ${serverName}`, { category: 'mcp' });
    }
  }

  /**
   * Helper: Remove server from persisted disabled list
   */
  private async _removeFromDisabledServers(serverName: string): Promise<void> {
    if (!this.settings) return;
    
    try {
      const current = await this.settings.get('mcp_disabled_servers', []) ?? [];
      const index = current.indexOf(serverName);
      if (index >= 0) {
        current.splice(index, 1);
        await this.settings.set('mcp_disabled_servers', current);
        this.logger.info(`[MCP] Removed persisted disabled state for server: ${serverName}`, { category: 'mcp' });
      }
    } catch (err) {
      this.logger.warn(`[MCP] Failed to remove disabled state for ${serverName}`, { category: 'mcp' });
    }
  }

  /**
   * Return a list of configured servers from the mcp-config.json file along
   * with runtime status information (connected, persisted disabled, tools list)
   */
  async getConfiguredServers(): Promise<Array<MCPServer & { enabled: boolean; connected: boolean; persistedDisabled: boolean }>> {
    try {
      const config = await this.loadConfig();
      const serversConfig = config?.mcpServers || {};
      const persistedDisabledResult = await this.settings?.get('mcp_disabled_servers', []);
      const persistedDisabled: string[] = Array.isArray(persistedDisabledResult) ? persistedDisabledResult : [];

      const servers: Array<MCPServer & { enabled: boolean; connected: boolean; persistedDisabled: boolean }> = [];
      for (const [name, serverConfig] of Object.entries(serversConfig)) {
        const enabled = serverConfig?.enabled !== false;
        const connected = this.clients.has(name);
        const rawTools = this.getToolsByServer(name) || [];
        const tools = rawTools.map(t => ({ key: t.name, name: t.name, description: t.description || '' }));
        const toolCount = tools.length;
        const persistedDisabledFlag = persistedDisabled.includes(name);

        servers.push({
          name,
          toolCount,
          tools,
          enabled,
          connected,
          persistedDisabled: persistedDisabledFlag
        });
      }

      // Include any connected servers that are not present in config (shouldn't normally happen)
      for (const serverName of Array.from(this.clients.keys())) {
        if (!servers.find((s) => s.name === serverName)) {
          const rawTools = this.getToolsByServer(serverName) || [];
          const tools = rawTools.map(t => ({ key: t.name, name: t.name, description: t.description || '' }));
          servers.push({
            name: serverName,
            toolCount: tools.length,
            tools,
            enabled: true,
            connected: true,
            persistedDisabled: false
          });
        }
      }

      // Sort by name for stable ordering
      servers.sort((a, b) => a.name.localeCompare(b.name));
      return servers;
    } catch (err) {
      this.logger.warn('[MCP] Failed to get configured servers', { category: 'mcp', error: err instanceof Error ? err.message : String(err) });
      // Return available servers with default enabled/connected/persistedDisabled values
      return this.getAvailableServers().map(s => ({ ...s, enabled: true, connected: true, persistedDisabled: false }));
    }
  }
}