
import React from 'react';
import type { Tool } from '../types';

interface ToolTagProps {
  tool: Tool;
  onToggle: () => void;
  disabled: boolean;
}

export const ToolTag: React.FC<ToolTagProps> = ({ tool, onToggle, disabled }) => {
  const baseClasses = 'px-3 py-1 rounded-md text-sm font-medium transition-colors duration-200 border';
  const enabledClasses = 'bg-gray-300 dark:bg-gray-600 text-gray-900 dark:text-gray-100 border-gray-400 dark:border-gray-500';
  const disabledClasses = 'bg-gray-200 dark:bg-gray-700 text-gray-700 dark:text-gray-300 border-gray-300 dark:border-gray-600';
  const parentDisabledClasses = 'cursor-not-allowed opacity-50';
  const clickableClasses = 'cursor-pointer hover:bg-gray-300 dark:hover:bg-gray-500';

  const finalClasses = `${baseClasses} ${tool.enabled ? enabledClasses : disabledClasses} ${disabled ? parentDisabledClasses : clickableClasses}`;
  
  return (
    <button
      onClick={onToggle}
      disabled={disabled}
      className={finalClasses}
    >
      {tool.name}
    </button>
  );
};
