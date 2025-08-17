import React, { useState, FormEvent, useEffect } from 'react';
import { createPortal } from 'react-dom';
import type { McpServer } from '../types';
import { GitBranch, Settings, X, LoaderCircle, Trash2 } from 'lucide-react';

interface AddServerModalProps {
  isOpen: boolean;
  onClose: () => void;
  onAddServer: (newServer: Omit<McpServer, 'id' | 'tools' | 'enabled'>) => void;
}

type Tab = 'git' | 'manual';
type AnalysisState = 'idle' | 'loading' | 'success' | 'error';

interface AnalysisResult {
    name: string;
    command: string;
    dependencies: string[];
    envVars: string[];
}

export const AddServerModal: React.FC<AddServerModalProps> = ({ isOpen, onClose, onAddServer }) => {
  const [activeTab, setActiveTab] = useState<Tab>('git');
  
  // Git Tab State
  const [repoUrl, setRepoUrl] = useState('');
  const [analysisState, setAnalysisState] = useState<AnalysisState>('idle');
  const [analysisResult, setAnalysisResult] = useState<AnalysisResult | null>(null);
  const [envVarValues, setEnvVarValues] = useState<Record<string, string>>({});

  // Manual Tab State
  const [manualName, setManualName] = useState('');
  const [manualInitial, setManualInitial] = useState('');
  const [manualCommand, setManualCommand] = useState('');
  const [manualDeps, setManualDeps] = useState('');
  const [manualEnvVars, setManualEnvVars] = useState<{name: string, value: string}[]>([]);


  useEffect(() => {
    // Reset state when modal is closed or opened
    if (isOpen) {
        setRepoUrl('');
        setAnalysisState('idle');
        setAnalysisResult(null);
        setEnvVarValues({});
        setManualName('');
        setManualInitial('');
        setManualCommand('');
        setManualDeps('');
        setManualEnvVars([]);
        setActiveTab('git');
    }
  }, [isOpen]);

  useEffect(() => {
    if (manualName) {
        setManualInitial(manualName.charAt(0).toUpperCase());
    } else {
        setManualInitial('');
    }
  }, [manualName])

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        onClose();
      }
    };

    if (isOpen) {
      document.addEventListener('keydown', handleKeyDown);
    }

    return () => {
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, [isOpen, onClose]);

  if (!isOpen) return null;

  const handleAnalyzeRepo = async () => {
    if (!repoUrl) return;
    setAnalysisState('loading');
    setAnalysisResult(null);

    // Simulate API call
    await new Promise(resolve => setTimeout(resolve, 2000));

    if (repoUrl.includes('fail')) {
        setAnalysisState('error');
    } else {
        const mockResult: AnalysisResult = {
            name: repoUrl.split('/').pop()?.replace('.git', '') || 'my-mcp-server',
            command: 'python main.py --port 8080',
            dependencies: ['firecrawl-py', 'fastapi', 'uvicorn'],
            envVars: ['API_KEY', 'ANOTHER_SECRET']
        };
        setAnalysisResult(mockResult);
        setAnalysisState('success');
        const initialEnvValues: Record<string, string> = {};
        mockResult.envVars.forEach(v => initialEnvValues[v] = '');
        setEnvVarValues(initialEnvValues);
    }
  };
  
  const handleGitSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (!analysisResult) return;
    // In a real app, envVarValues would be sent to the backend securely
    console.log('Submitted ENV Vars:', envVarValues);
    onAddServer({
        name: analysisResult.name,
        initial: analysisResult.name.charAt(0).toUpperCase(),
        command: analysisResult.command,
    });
  };

  const handleManualSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (!manualName) return;
    // In a real app, manualDeps and manualEnvVars would be used by the backend
    console.log('Manual Deps:', manualDeps);
    console.log('Manual ENV Vars:', manualEnvVars);
    onAddServer({
        name: manualName,
        initial: manualInitial,
        command: manualCommand,
    });
  };

  const handleAddEnvVar = () => {
    setManualEnvVars([...manualEnvVars, { name: '', value: '' }]);
  };

  const handleRemoveEnvVar = (index: number) => {
    setManualEnvVars(manualEnvVars.filter((_, i) => i !== index));
  };
  
  const handleEnvVarChange = (index: number, field: 'name' | 'value', value: string) => {
    const newEnvVars = [...manualEnvVars];
    newEnvVars[index][field] = value;
    setManualEnvVars(newEnvVars);
  };


  const renderGitTab = () => (
    <form onSubmit={handleGitSubmit}>
        <p className="text-sm text-gray-500 dark:text-gray-400 mb-4">
            Enter the URL of a Git repository. We'll attempt to automatically discover its configuration by looking for conventional files like <code>mcp_manifest.json</code> and <code>requirements.txt</code>.
        </p>
        <div className="flex space-x-2">
            <input 
                type="text"
                value={repoUrl}
                onChange={e => setRepoUrl(e.target.value)}
                placeholder="https://github.com/user/repo.git"
                className="flex-grow bg-gray-100 dark:bg-gray-800 border border-gray-300 dark:border-gray-600 rounded-md px-3 py-2 text-sm focus:ring-2 focus:ring-primary focus:outline-none"
            />
            <button 
                type="button"
                onClick={handleAnalyzeRepo}
                disabled={analysisState === 'loading' || !repoUrl}
                className="flex items-center justify-center bg-primary text-white font-medium px-4 py-2 rounded-md hover:bg-primary/90 disabled:bg-primary/50 disabled:cursor-not-allowed transition-colors"
            >
                {analysisState === 'loading' ? <LoaderCircle className="animate-spin h-5 w-5" /> : 'Analyze'}
            </button>
        </div>
        
        {analysisState === 'loading' && (
            <div className="text-center mt-6 text-sm text-gray-500 dark:text-gray-400">
                <p>Analyzing repository...</p>
            </div>
        )}

        {analysisState === 'error' && (
             <div className="bg-red-900/20 border border-red-400/30 text-red-300 px-4 py-3 rounded-lg mt-6 text-sm">
                <p><strong>Analysis Failed.</strong> Could not find a valid <code>mcp_manifest.json</code> in the repository. Please check the URL or try the Manual Setup.</p>
             </div>
        )}

        {analysisState === 'success' && analysisResult && (
            <div className="mt-6 space-y-4">
                <h3 className="font-semibold text-lg text-gray-900 dark:text-gray-100">Analysis Complete</h3>
                <div className="space-y-2 p-4 bg-gray-100/50 dark:bg-gray-800/50 rounded-md border border-gray-200 dark:border-gray-700">
                    <p><strong>Name:</strong> {analysisResult.name}</p>
                    <p><strong>Start Command:</strong> <code>{analysisResult.command}</code></p>
                    <p><strong>Dependencies Found:</strong> {analysisResult.dependencies.join(', ')}</p>
                </div>
                {analysisResult.envVars.length > 0 && (
                    <div>
                        <h4 className="font-medium mb-2 text-gray-800 dark:text-gray-200">Required Settings</h4>
                        <div className="space-y-3">
                        {analysisResult.envVars.map(varName => (
                            <div key={varName}>
                                <label htmlFor={varName} className="block text-sm font-medium text-gray-600 dark:text-gray-400 mb-1">{varName}</label>
                                <input
                                    type="password"
                                    id={varName}
                                    value={envVarValues[varName] || ''}
                                    onChange={(e) => setEnvVarValues({...envVarValues, [varName]: e.target.value})}
                                    className="w-full bg-gray-100 dark:bg-gray-800 border border-gray-300 dark:border-gray-600 rounded-md px-3 py-2 text-sm focus:ring-2 focus:ring-primary focus:outline-none"
                                />
                            </div>
                        ))}
                        </div>
                    </div>
                )}
                <button 
                    type="submit"
                    className="w-full bg-green-500 text-white font-bold px-4 py-2.5 rounded-md hover:bg-green-600 transition-colors"
                >
                    Install Server
                </button>
            </div>
        )}

    </form>
  );

  const renderManualTab = () => (
    <form onSubmit={handleManualSubmit} className="space-y-4">
        <p className="text-sm text-gray-500 dark:text-gray-400 mb-4">
            Manually configure all the settings for your MCP server.
        </p>
        <div>
            <label htmlFor="manualName" className="block text-sm font-medium text-gray-600 dark:text-gray-400 mb-1">Server Name</label>
            <div className="flex items-center space-x-2">
                <input
                    id="manualInitial"
                    type="text"
                    value={manualInitial}
                    readOnly
                    className="w-12 text-center bg-gray-200 dark:bg-gray-700 border border-gray-300 dark:border-gray-600 rounded-md px-3 py-2 text-sm font-semibold"
                />
                <input
                    id="manualName"
                    type="text"
                    value={manualName}
                    onChange={(e) => setManualName(e.target.value)}
                    placeholder="my-custom-server"
                    required
                    className="flex-grow bg-gray-100 dark:bg-gray-800 border border-gray-300 dark:border-gray-600 rounded-md px-3 py-2 text-sm focus:ring-2 focus:ring-primary focus:outline-none"
                />
            </div>
        </div>

        <div>
            <label htmlFor="manualCommand" className="block text-sm font-medium text-gray-600 dark:text-gray-400 mb-1">Start Command</label>
            <input
                id="manualCommand"
                type="text"
                value={manualCommand}
                onChange={(e) => setManualCommand(e.target.value)}
                placeholder="e.g., python main.py"
                className="w-full bg-gray-100 dark:bg-gray-800 border border-gray-300 dark:border-gray-600 rounded-md px-3 py-2 text-sm focus:ring-2 focus:ring-primary focus:outline-none"
            />
        </div>

        <div>
            <label htmlFor="manualDeps" className="block text-sm font-medium text-gray-600 dark:text-gray-400 mb-1">Dependencies</label>
            <textarea
                id="manualDeps"
                value={manualDeps}
                onChange={(e) => setManualDeps(e.target.value)}
                rows={4}
                placeholder="Paste contents of requirements.txt or list one package per line..."
                className="w-full bg-gray-100 dark:bg-gray-800 border border-gray-300 dark:border-gray-600 rounded-md px-3 py-2 text-sm focus:ring-2 focus:ring-primary focus:outline-none font-mono"
            />
        </div>

        <div>
            <h4 className="font-medium mb-2 text-gray-800 dark:text-gray-200">Environment Variables</h4>
            <div className="space-y-2">
                {manualEnvVars.map((envVar, index) => (
                    <div key={index} className="flex items-center space-x-2">
                        <input
                            type="text"
                            placeholder="Variable Name"
                            value={envVar.name}
                            onChange={e => handleEnvVarChange(index, 'name', e.target.value)}
                            className="w-1/3 bg-gray-100 dark:bg-gray-800 border border-gray-300 dark:border-gray-600 rounded-md px-3 py-2 text-sm focus:ring-2 focus:ring-primary focus:outline-none"
                        />
                        <input
                            type="password"
                            placeholder="Value"
                            value={envVar.value}
                             onChange={e => handleEnvVarChange(index, 'value', e.target.value)}
                            className="flex-grow bg-gray-100 dark:bg-gray-800 border border-gray-300 dark:border-gray-600 rounded-md px-3 py-2 text-sm focus:ring-2 focus:ring-primary focus:outline-none"
                        />
                         <button type="button" onClick={() => handleRemoveEnvVar(index)} className="p-2 text-gray-400 hover:text-red-500 rounded-md">
                            <Trash2 className="h-4 w-4" />
                        </button>
                    </div>
                ))}
            </div>
            <button type="button" onClick={handleAddEnvVar} className="mt-2 text-sm text-primary hover:underline">
                + Add Variable
            </button>
        </div>


        <button
            type="submit"
            disabled={!manualName}
            className="w-full bg-primary text-white font-bold px-4 py-2.5 rounded-md hover:bg-primary/90 disabled:bg-primary/50 disabled:cursor-not-allowed transition-colors"
        >
            Add Server
        </button>
    </form>
  );

  const modalContent = (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm" onClick={onClose}>
      <div 
        className="relative w-full max-w-2xl bg-white dark:bg-[#0c0c0f] border border-gray-200 dark:border-gray-800 rounded-xl shadow-2xl m-4 text-gray-800 dark:text-gray-200"
        onClick={e => e.stopPropagation()}
      >
        <div className="p-6 border-b border-gray-200 dark:border-gray-800 flex justify-between items-center">
            <h2 className="text-xl font-semibold">Add New MCP Server</h2>
            <button onClick={onClose} className="p-1 rounded-full text-gray-500 hover:bg-gray-200 dark:hover:bg-gray-700">
                <X size={20} />
            </button>
        </div>

        <div className="p-2 bg-gray-100 dark:bg-gray-900/50 m-6 rounded-lg">
            <div className="flex space-x-1">
                 <TabButton
                    icon={<GitBranch size={16} />}
                    label="Install from Git"
                    isActive={activeTab === 'git'}
                    onClick={() => setActiveTab('git')}
                />
                <TabButton
                    icon={<Settings size={16} />}
                    label="Manual Setup"
                    isActive={activeTab === 'manual'}
                    onClick={() => setActiveTab('manual')}
                />
            </div>
        </div>
        
        <div className="px-6 pb-6">
            {activeTab === 'git' ? renderGitTab() : renderManualTab()}
        </div>
      </div>
    </div>
  );

  return createPortal(modalContent, document.body);
};

interface TabButtonProps {
    icon: React.ReactNode;
    label: string;
    isActive: boolean;
    onClick: () => void;
}

const TabButton: React.FC<TabButtonProps> = ({ icon, label, isActive, onClick }) => {
    return (
        <button
            onClick={onClick}
            className={`flex-1 flex items-center justify-center space-x-2 px-4 py-2.5 rounded-md text-sm font-medium transition-colors duration-200
            ${isActive
                ? 'bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 shadow-sm'
                : 'text-gray-500 dark:text-gray-400 hover:bg-white/50 dark:hover:bg-gray-800/40'
            }`}
        >
            {icon}
            <span>{label}</span>
        </button>
    )
}