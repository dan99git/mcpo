/**
 * Tool Registry Service
 * 
 * ROLE:
 * The persistent source of truth for tool metadata and usage statistics in the Backend.
 * Unlike `ToolDiscoveryService` (which is ephemeral/request-based), this service
 * maintains long-lived records of tools, their risk levels, and usage history.
 * 
 * ARCHITECTURE:
 * - Singleton service in the `ServiceContainer`.
 * - Hydrated via `syncRegistry` which pulls from `ToolDiscoveryService`.
 * - Used by the UI (via API) to display tool lists, icons, and descriptions.
 * 
 * KEY FEATURES:
 * - Risk Level Tracking: `riskLevel` (low/medium/high).
 * - Usage Analytics: `recordToolUsage`.
 * - Origin Tracking: Distinguishes between `baseline`, `panel`, and `mcp` tools.
 * 
 * SAFETY:
 * - Safe to read (`getToolMetadata`).
 * - Writes are limited to metadata updates (registration, usage stats).
 */
import { ToolDiscoveryService } from './tool-discovery-service';
import { LoggingService } from '../../core/logging';

export interface ToolMetadata {
  name: string;
  origin: 'baseline' | 'panel' | 'mcp';
  panelId?: string;
  description: string;
  riskLevel?: 'low' | 'medium' | 'high';
  requiresConfirmation?: boolean;
  lastUsed?: Date;
  usageCount?: number;
}

export class ToolRegistryService {
  private discoveryService: ToolDiscoveryService;
  private toolMetadata = new Map<string, ToolMetadata>();
  private logger: LoggingService;

  constructor(
    discoveryService: ToolDiscoveryService,
    logger: LoggingService
  ) {
    this.discoveryService = discoveryService;
    this.logger = logger;
  }

  /**
   * Register a tool with metadata
   */
  registerTool(metadata: ToolMetadata): void {
    if (this.toolMetadata.has(metadata.name)) {
      this.logger.warn('[ToolRegistry] Duplicate tool registration detected; overwriting', { toolName: metadata.name });
    }
    this.toolMetadata.set(metadata.name, metadata);
  }

  /**
   * Get tool metadata
   */
  getToolMetadata(toolName: string): ToolMetadata | null {
    return this.toolMetadata.get(toolName) || null;
  }

  /**
   * Get all registered tools
   */
  getAllTools(): ToolMetadata[] {
    return Array.from(this.toolMetadata.values());
  }

  /**
   * Get tools by origin
   */
  getToolsByOrigin(origin: 'baseline' | 'panel' | 'mcp'): ToolMetadata[] {
    return Array.from(this.toolMetadata.values()).filter(t => t.origin === origin);
  }

  /**
   * Update tool usage statistics
   */
  recordToolUsage(toolName: string): void {
    const metadata = this.toolMetadata.get(toolName);
    if (metadata) {
      metadata.lastUsed = new Date();
      metadata.usageCount = (metadata.usageCount || 0) + 1;
    }
  }

  /**
   * Sync registry with discovery service
   */
  syncRegistry(panelId: string | null): void {
    const discovered = this.discoveryService.discoverTools(panelId);
    
    // Update baseline tools
    discovered.baseline.forEach(tool => {
      this.registerTool({
        name: tool.name,
        origin: 'baseline',
        description: tool.description,
        riskLevel: 'low',
      });
    });

    // Update panel tools
    discovered.panel.forEach(tool => {
      this.registerTool({
        name: tool.name,
        origin: 'panel',
        panelId: panelId || undefined,
        description: tool.description,
        riskLevel: 'medium',
      });
    });

    // Update MCP tools
    discovered.mcp.forEach(tool => {
      this.registerTool({
        name: tool.name,
        origin: 'mcp',
        description: tool.description,
        riskLevel: 'low',
      });
    });

    this.logger.info('[ToolRegistry] Registry synced', {
      baseline: discovered.baseline.length,
      panel: discovered.panel.length,
      mcp: discovered.mcp.length,
      panelId,
    });
  }
}
