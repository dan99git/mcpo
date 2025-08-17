
export interface Tool {
  name: string;
  enabled: boolean; // client-side toggle only for now
}

export interface McpServer {
  id: string;            // synthetic (name + timestamp or just name)
  initial: string;       // first letter convenience
  name: string;          // server name (matches backend mount)
  type: string;          // backend reported type
  connected: boolean;    // backend connectivity flag
  basePath: string;      // mount base path (e.g. /time/)
  command?: string;      // optional config hint (not supplied by backend yet)
  enabled: boolean;      // client-side enabled flag (backend does not enforce yet)
  tools: Tool[];         // discovered tools
}
