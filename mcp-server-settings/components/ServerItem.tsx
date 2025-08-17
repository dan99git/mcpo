import React, { useState, useMemo } from 'react';
import type { McpServer } from '../types';
import { ToggleSwitch } from './ToggleSwitch';
import { ToolTag } from './ToolTag';
import { ChevronDown } from 'lucide-react';

interface ServerItemProps {
  server: McpServer;
  onServerToggle: (serverId: string) => void;
  onToolToggle: (serverId: string, toolName: string) => void;
  onRestart?: () => void; // optional restart
  onRemove?: () => void;
}

export const ServerItem: React.FC<ServerItemProps> = ({ server, onServerToggle, onToolToggle, onRestart, onRemove }) => {
  const [isExpanded, setIsExpanded] = useState(server.name === 'browser-tools');

  const enabledToolsCount = useMemo(() => {
    return server.tools.filter(tool => tool.enabled).length;
  }, [server.tools]);

  const hasTools = server.tools.length > 0;

  const getStatus = () => {
    if (!server.enabled) {
      return { text: 'Disabled', color: 'bg-gray-500', hasDot: false };
    }
    if (!hasTools) {
      return { text: 'No tools or prompts', color: 'bg-red-500', hasDot: true };
    }
    return { text: `${enabledToolsCount} tools enabled`, color: 'bg-green-500', hasDot: true };
  };

  const status = getStatus();

  const handleToggleExpand = () => {
    if (hasTools) {
      setIsExpanded(!isExpanded);
    }
  };

  return (
    <div className={`bg-white dark:bg-gray-800/40 rounded-lg transition-all duration-300 ${server.enabled ? 'opacity-100' : 'opacity-60'}`}>
      <div className="flex items-center p-4">
        <div className="w-10 h-10 bg-gray-200 dark:bg-gray-700 rounded-full flex items-center justify-center mr-4 shrink-0">
          <span className="text-xl font-medium text-gray-700 dark:text-gray-300">{server.initial}</span>
        </div>
        <div className="flex-grow">
          <div 
            className={`flex items-center space-x-2 ${hasTools ? 'cursor-pointer' : ''}`}
            onClick={handleToggleExpand}
          >
            <p className="font-medium text-gray-900 dark:text-gray-100">{server.name}</p>
            {status.hasDot && (
              <span className={`h-2 w-2 rounded-full ${status.color}`}></span>
            )}
          </div>
          <div 
            className={`flex items-center text-sm text-gray-500 dark:text-gray-400 ${hasTools ? 'cursor-pointer' : ''}`}
            onClick={handleToggleExpand}
          >
            <span>{server.command ? server.command : status.text}</span>
            {hasTools && (
               <ChevronDown
                className={`ml-1 h-4 w-4 transform transition-transform duration-200 ${isExpanded ? 'rotate-180' : ''}`}
              />
            )}
          </div>
        </div>
        <div className="ml-4 flex items-center gap-2">
          <ToggleSwitch
            checked={server.enabled}
            onChange={() => onServerToggle(server.id)}
          />
          {onRestart && (
            <button
              onClick={onRestart}
              className="text-xs px-2 py-1 rounded bg-gray-200 dark:bg-gray-700 hover:bg-gray-300 dark:hover:bg-gray-600 text-gray-700 dark:text-gray-200"
              title="Reinitialize this server"
            >
              Restart
            </button>
          )}
          {onRemove && (
            <button
              onClick={onRemove}
              className="text-xs px-2 py-1 rounded bg-red-500/80 hover:bg-red-600 text-white"
              title="Remove this server"
            >
              Remove
            </button>
          )}
        </div>
      </div>
      {hasTools && isExpanded && (
        <div className="px-4 pb-4 pt-2 border-t border-gray-200 dark:border-gray-700/50">
          <div className="flex flex-wrap gap-2">
            {server.tools.map(tool => (
              <ToolTag
                key={tool.name}
                tool={tool}
                onToggle={() => onToolToggle(server.id, tool.name)}
                disabled={!server.enabled}
              />
            ))}
          </div>
          <button 
            onClick={() => setIsExpanded(false)}
            className="text-sm text-gray-500 dark:text-gray-400 mt-4 flex items-center hover:text-gray-700 dark:hover:text-gray-200"
          >
            Show less
            <ChevronDown
              className="ml-1 h-4 w-4 transform rotate-180"
            />
          </button>
        </div>
      )}
    </div>
  );
};
