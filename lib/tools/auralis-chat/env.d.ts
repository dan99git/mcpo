/**
 * Environment Type Definitions
 * 
 * ARCHITECTURE NOTES:
 * - Extends Vite's ImportMetaEnv with project-specific variables
 * - Ensures type safety for import.meta.env usage
 */

/// <reference types="vite/client" />

interface ImportMetaEnv {
    readonly VITE_GATEWAY_PORT: string;
    readonly VITE_API_BASE_URL: string;
    readonly VITE_DEV_AUTH_USER_ID: string;
}

interface ImportMeta {
    readonly env: ImportMetaEnv;
}
