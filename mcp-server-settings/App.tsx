import React, { useState, useEffect, useMemo, useCallback } from 'react';
import { INITIAL_SERVERS } from './constants';
import type { McpServer } from './types';
import { ServerItem } from './components/ServerItem';
import { Sun, Moon, AlertTriangle, ExternalLink, RefreshCw, Plus, Trash2 } from 'lucide-react';
import { AddServerModal } from './components/AddServerModal';

const App: React.FC = () => {
  const [servers, setServers] = useState<McpServer[]>(INITIAL_SERVERS);
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [theme, setTheme] = useState<'light' | 'dark'>('dark');
  const [configPath, setConfigPath] = useState<string | null>(null);
  const [reloading, setReloading] = useState(false);
  const [isModalOpen, setIsModalOpen] = useState(false);

  useEffect(() => {
    const root = window.document.documentElement;
    root.classList.remove(theme === 'light' ? 'dark' : 'light');
    root.classList.add(theme);
  }, [theme]);

  const toggleTheme = () => {
    setTheme(theme === 'light' ? 'dark' : 'light');
  };

  const handleServerToggle = async (serverId: string) => {
    const target = servers.find(s => s.id === serverId);
    if (!target) return;
    const newEnabled = !target.enabled;
    try {
      const ep = newEnabled ? '/_meta/servers/' + encodeURIComponent(target.name) + '/enable' : '/_meta/servers/' + encodeURIComponent(target.name) + '/disable';
      const r = await fetch(ep, { method: 'POST' });
      if (!r.ok) throw new Error('Server toggle failed');
      const body = await r.json().catch(()=>({}));
      if(!body.ok) throw new Error(body.error?.message||'Server toggle failed');
      setServers(prev => prev.map(s => s.id===serverId ? { ...s, enabled: newEnabled } : s));
    } catch(e:any){ setError(e.message); }
  };
  const handleToolToggle = async (serverId: string, toolName: string) => {
    const target = servers.find(s => s.id === serverId);
    if(!target) return;
    const tool = target.tools.find(t => t.name === toolName);
    if(!tool) return;
    const newEnabled = !tool.enabled;
    try {
      const ep = `/_meta/servers/${encodeURIComponent(target.name)}/tools/${encodeURIComponent(toolName)}/${newEnabled ? 'enable' : 'disable'}`;
      const r = await fetch(ep, { method: 'POST' });
      if(!r.ok) throw new Error('Tool toggle failed');
      const body = await r.json().catch(()=>({}));
      if(!body.ok) throw new Error(body.error?.message||'Tool toggle failed');
      setServers(prev => prev.map(s => s.id===serverId ? { ...s, tools: s.tools.map(t => t.name===toolName ? { ...t, enabled: newEnabled } : t) } : s));
    } catch(e:any){ setError(e.message); }
  };

  const handleAddServer = async (newServer: { name: string; initial: string; command?: string }) => {
    try {
      const payload: any = { name: newServer.name };
      if (newServer.command) payload.command = newServer.command;
      const r = await fetch('/_meta/servers', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
      const body = await r.json();
      if(!r.ok || !body.ok) throw new Error(body.error?.message || 'Add failed');
      setIsModalOpen(false);
      await fetchServers();
    } catch(e:any){ setError(e.message); }
  };

  const handleRemoveServer = async (name: string) => {
    if(!confirm(`Remove server ${name}?`)) return;
    try {
      const r = await fetch(`/_meta/servers/${encodeURIComponent(name)}`, { method: 'DELETE' });
      const body = await r.json().catch(()=>({}));
      if(!r.ok || !body.ok) throw new Error(body.error?.message || 'Remove failed');
      await fetchServers();
    } catch(e:any){ setError(e.message); }
  };

  const fetchServers = useCallback(async () => {
    setLoading(true); setError(null);
    try {
      const r = await fetch('/_meta/servers');
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      if (!data.ok) throw new Error(data.error?.message || 'Failed');
      const metaServers: McpServer[] = await Promise.all(
        data.servers.map(async (s: any) => {
          // Fetch tools
            let tools: { name: string; enabled: boolean }[] = [];
            try {
              const tr = await fetch(`/_meta/servers/${s.name}/tools`);
              if (tr.ok) {
                const tdata = await tr.json();
                if (tdata.ok) tools = tdata.tools.map((t: any) => ({ name: t.name, enabled: t.enabled }));
              }
            } catch {}
          return {
            id: s.name,
            name: s.name,
            initial: s.name.charAt(0).toUpperCase(),
            type: s.type,
            connected: s.connected,
            basePath: s.basePath,
            enabled: s.enabled !== false,
            tools,
          } as McpServer;
        })
      );
      // merge with existing to preserve optimistic toggles while waiting
      setServers(prev => metaServers.map(ms => {
        const old = prev.find(p => p.id===ms.id);
        if(!old) return ms;
        return { ...ms, enabled: ms.enabled, tools: ms.tools.map(t => {
          const oldTool = old.tools.find(ot => ot.name===t.name);
          return oldTool ? { ...t, enabled: oldTool.enabled } : t;
        }) };
      }));
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [setServers]);

  useEffect(() => { fetchServers(); const id = setInterval(fetchServers, 8000); return () => clearInterval(id); }, [fetchServers]);
  useEffect(() => {
    (async () => {
      try { const r = await fetch('/_meta/config'); if(r.ok){ const b = await r.json(); if(b.ok) setConfigPath(b.configPath||null);} } catch {}
    })();
  }, []);

  const openConfig = () => {
    if(!configPath){ return; }
    // If protocol running inside VS Code webview/desktop with vscode:// handler
    const vscodeUrl = `vscode://file/${encodeURIComponent(configPath)}`;
    // Fallback: attempt standard file:// (may be blocked by browser)
    window.open(vscodeUrl, '_blank');
  };


  const totalEnabledTools = useMemo(() => servers.reduce((t, s) => t + s.tools.length, 0), [servers]);

  const showWarning = totalEnabledTools > 40;

  const triggerReload = async () => {
    if (reloading) return;
    setReloading(true);
    try {
      const r = await fetch('/_meta/reload', { method: 'POST' });
      const data = await r.json().catch(() => ({}));
      if (!r.ok || !data.ok) {
        throw new Error(data?.error?.message || `Reload failed (${r.status})`);
      }
      await fetchServers();
    } catch (e:any) {
      setError(e.message || 'Reload failed');
    } finally {
      setReloading(false);
    }
  };

  const restartAll = async () => {
    // For config-driven: trigger reload (already re-inits new) then fetch
    await triggerReload();
  };

  const restartServer = async (name: string) => {
    try {
      const r = await fetch(`/_meta/reinit/${encodeURIComponent(name)}`, { method: 'POST' });
      const data = await r.json().catch(() => ({}));
      if (!r.ok || !data.ok) throw new Error(data?.error?.message || 'Reinit failed');
      await fetchServers();
    } catch (e:any) {
      setError(e.message);
    }
  };

  return (
    <div className="min-h-screen text-gray-800 dark:text-gray-200 transition-colors duration-300 font-sans">
      <div className="max-w-4xl mx-auto p-4 sm:p-6 md:p-8">
        <header className="flex justify-between items-center mb-6">
          <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100">MCP Tools</h1>
          <div className="flex items-center gap-2">
            <button
              onClick={triggerReload}
              disabled={reloading}
              className="p-2 rounded-full text-gray-500 dark:text-gray-400 hover:bg-gray-200 dark:hover:bg-gray-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors duration-200"
              aria-label="Reload config"
              title="Force config reload"
            >
              <RefreshCw size={18} className={reloading ? 'animate-spin' : ''} />
            </button>
            <button
              onClick={toggleTheme}
              className="p-2 rounded-full text-gray-500 dark:text-gray-400 hover:bg-gray-200 dark:hover:bg-gray-700 transition-colors duration-200"
              aria-label="Toggle theme"
            >
              {theme === 'light' ? <Moon size={20} /> : <Sun size={20} />}
            </button>
          </div>
        </header>

        {showWarning && (
            <div className="bg-yellow-100/20 dark:bg-yellow-900/20 border border-yellow-400/30 text-yellow-700 dark:text-yellow-300 px-4 py-3 rounded-lg relative mb-6 flex items-start space-x-3">
              <AlertTriangle className="h-5 w-5 mt-0.5 text-yellow-500" />
              <div>
                <strong className="font-bold">Exceeding total tools limit</strong>
                <p className="block sm:inline text-sm">
                  You have {totalEnabledTools} tools from enabled servers. Too many tools can degrade performance, and some models may not respect more than 40 tools.
                </p>
              </div>
            </div>
        )}

        <div className="space-y-2 min-h-[120px]">
          {loading && servers.length === 0 && (
            <div className="text-sm text-gray-500 dark:text-gray-400">Loading servers…</div>
          )}
          {error && (
            <div className="text-sm text-red-500">Error: {error}</div>
          )}
          {servers.map(server => (
            <ServerItem
              key={server.id}
              server={server}
              onServerToggle={handleServerToggle}
              onToolToggle={handleToolToggle}
              // @ts-ignore add restart & remove
              onRestart={() => restartServer(server.name)}
              // @ts-ignore
              onRemove={() => handleRemoveServer(server.name)}
            />
          ))}
          {!loading && servers.length === 0 && !error && (
            <div className="text-sm text-gray-500 dark:text-gray-400">No servers detected.</div>
          )}
        </div>

  <div className="mt-4 border-t border-gray-200 dark:border-gray-800 pt-4 space-y-2">
          {configPath && (
            <div className="flex gap-2">
              <button
                onClick={openConfig}
                className="flex-1 flex items-center p-4 text-left text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800/50 rounded-lg transition-colors duration-200"
              >
                <div className="w-10 h-10 bg-gray-200 dark:bg-gray-700 rounded-full flex items-center justify-center mr-4">
                  <ExternalLink className="w-6 h-6" />
                </div>
                <div>
                  <p className="font-medium text-gray-800 dark:text-gray-200">Open Configuration</p>
                  <p className="text-xs truncate max-w-[180px] md:max-w-[260px]">{configPath}</p>
                </div>
              </button>
              <button
                onClick={restartAll}
                disabled={reloading}
                className="flex-1 flex items-center p-4 text-left text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800/50 rounded-lg transition-colors duration-200 disabled:opacity-50"
              >
                <div className="w-10 h-10 bg-gray-200 dark:bg-gray-700 rounded-full flex items-center justify-center mr-4">
                  <RefreshCw className={`w-6 h-6 ${reloading ? 'animate-spin' : ''}`} />
                </div>
                <div>
                  <p className="font-medium text-gray-800 dark:text-gray-200">Reload All</p>
                  <p className="text-xs">Reload config & reconnect</p>
                </div>
              </button>
            </div>
          )}
          {/* Add-server button */}
          <button 
              onClick={() => setIsModalOpen(true)}
              className="flex items-center w-full p-4 text-left text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800/50 rounded-lg transition-colors duration-200"
          >
              <div className="w-10 h-10 bg-gray-200 dark:bg-gray-700 rounded-full flex items-center justify-center mr-4">
                  <Plus className="w-6 h-6" />
              </div>
              <div>
                  <p className="font-medium text-gray-800 dark:text-gray-200">New MCP Server</p>
                  <p className="text-sm">Install from Git repository or set up manually</p>
              </div>
          </button>
        </div>
        <AddServerModal 
          isOpen={isModalOpen}
          onClose={() => setIsModalOpen(false)}
          onAddServer={handleAddServer as any}
        />
      </div>
    </div>
  );
};

export default App;
