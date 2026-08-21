/**
 * Frontend configuration. The API base URL is the only knob: it comes from
 * VITE_API_URL and defaults to the backend's local dev port (see vite.config.ts).
 */

export const API_BASE_URL: string = import.meta.env.VITE_API_URL;
