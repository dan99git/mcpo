/* MCPO Global Function Exports - Makes modular functions available to HTML onclick handlers */

// Theme functions (from theme.js)
window.toggleTheme = toggleTheme;

// Navigation functions (from navigation.js)  
window.openConfigPage = openConfigPage;

// Logs functions (from logs.js)
window.toggleAutoRefresh = toggleAutoRefresh;
window.refreshLogs = refreshLogs;
window.clearCurrentCategory = clearCurrentCategory;
window.clearAllLogs = clearAllLogs;
window.switchLogsCategory = switchLogsCategory;

// Server functions (from servers.js)
window.toggleServer = toggleServer;
window.toggleTool = toggleTool;
window.toggleServerExpansion = toggleServerExpansion;
window.restartAllServers = restartAllServers;

// API functions (from api.js)
window.saveConfigContent = saveConfigContent;
window.installDependencies = installDependencies;
window.saveRequirements = saveRequirements;

// Config functions (from config.js)
window.validateConfig = validateConfig;
window.resetConfig = resetConfig;